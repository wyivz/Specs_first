from __future__ import annotations

import base64
import json

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the API: fastapi uvicorn") from exc

from backend.api_models import (
    AsrTranscribeRequest,
    BrowserCommandRequest,
    DiscoverRequest,
    EventsSnapshotResponse,
    HealthResponse,
    ResumeAuthRequest,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskStatusResponse,
)
from backend.platform_health import build_platform_health
from backend.task_runner import task_manager
from schemas import to_dict

app = FastAPI(title="Specs-First", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    report = build_platform_health(probe_gemini=False)
    return HealthResponse(
        status=report.overall,
        service="specs-first",
        checked_at=report.checked_at,
        checks=[
            {
                "name": item.name,
                "status": item.status,
                "message": item.message,
                "details": item.details,
            }
            for item in report.checks
        ],
    )


@app.post("/discover")
def discover(payload: DiscoverRequest) -> dict:
    candidates = task_manager.discover(
        query=payload.query,
        category=payload.category,
        mode=payload.mode,
        source_urls=payload.source_urls,
    )
    return {"candidates": candidates}


@app.post("/tasks", response_model=TaskCreateResponse)
def create_task(payload: TaskCreateRequest) -> TaskCreateResponse:
    task_id = task_manager.start_task(
        query=payload.query,
        category=payload.category,
        selected_skus=payload.selected_skus,
        source_urls=payload.source_urls,
        mode=payload.mode,
        vault_path=payload.vault_path,
        discover_only=payload.discover_only,
        use_browser=payload.use_browser,
    )
    return TaskCreateResponse(task_id=task_id, state="RUNNING")


@app.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str) -> TaskStatusResponse:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(task_id=task_id, state=record.state, error=record.error)


@app.get("/tasks/{task_id}/events")
def stream_events(task_id: str) -> StreamingResponse:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    def event_stream():
        for event in task_manager.event_bus.subscribe(task_id):
            yield f"data: {json.dumps(to_dict(event), ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/tasks/{task_id}/events/snapshot", response_model=EventsSnapshotResponse)
def events_snapshot(task_id: str) -> EventsSnapshotResponse:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    events = [to_dict(event) for event in task_manager.event_bus.stream(task_id)]
    return EventsSnapshotResponse(task_id=task_id, events=events)


@app.get("/tasks/{task_id}/result")
def get_result(task_id: str) -> dict:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    if not record.result:
        return {"task_id": task_id, "state": record.state, "error": record.error}
    result = record.result
    return {
        "task_id": result.task_id,
        "state": result.state.value,
        "candidates": [to_dict(candidate) for candidate in result.candidates],
        "matrix": {
            "columns": [to_dict(column) for column in result.matrix.columns],
            "rows": [to_dict(row) for row in result.matrix.rows],
        },
        "output_paths": [str(path) for path in result.output_paths],
        "diagnostics": result.diagnostics,
    }


@app.get("/tasks/{task_id}/diagnostics")
def get_diagnostics(task_id: str) -> dict:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    if record.result and record.result.diagnostics:
        return {"task_id": task_id, "records": record.result.diagnostics}
    return {"task_id": task_id, "records": []}


@app.post("/tasks/{task_id}/resume-auth")
def resume_auth(task_id: str, payload: ResumeAuthRequest | None = None) -> dict:
    payload = payload or ResumeAuthRequest()
    try:
        task_manager.resume_task(task_id, use_browser=payload.use_browser)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"task_id": task_id, "state": "RUNNING"}


@app.get("/tasks/{task_id}/checkpoint")
def get_checkpoint(task_id: str) -> dict:
    checkpoint = task_manager.checkpoint_store.load(task_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return to_dict(checkpoint)


@app.get("/tasks/{task_id}/browser/status")
def browser_status(task_id: str) -> dict:
    from collectors.embedded_browser import get_bridge

    bridge = get_bridge(task_id)
    if not bridge:
        return {"active": False}
    return {
        "active": True,
        "url": bridge.url,
        "solved": bridge.is_solved,
        "closed": bridge.is_closed,
        "error": bridge.error,
        "screenshot_seq": bridge.screenshot_seq,
    }


@app.get("/tasks/{task_id}/browser/screenshot")
def browser_screenshot(task_id: str) -> dict:
    from collectors.embedded_browser import get_bridge

    bridge = get_bridge(task_id)
    if not bridge:
        raise HTTPException(status_code=404, detail="No active embedded browser session for this task")
    frame = bridge.latest_screenshot()
    if frame is None:
        return {"task_id": task_id, "image_base64": "", "screenshot_seq": 0}
    return {
        "task_id": task_id,
        "image_base64": base64.b64encode(frame).decode("ascii"),
        "screenshot_seq": bridge.screenshot_seq,
    }


@app.post("/tasks/{task_id}/browser/command")
def browser_command(task_id: str, payload: BrowserCommandRequest) -> dict:
    from collectors.embedded_browser import get_bridge

    bridge = get_bridge(task_id)
    if not bridge:
        raise HTTPException(status_code=404, detail="No active embedded browser session for this task")
    if payload.action not in {"click", "type", "key", "scroll"}:
        raise HTTPException(status_code=400, detail="action must be one of click/type/key/scroll")
    kwargs = payload.model_dump(exclude={"action"}, exclude_none=True)
    bridge.submit_command(payload.action, **kwargs)
    return {"task_id": task_id, "queued": payload.action}


@app.get("/asr/status")
def asr_status() -> dict:
    from collectors.asr import check_readiness

    readiness = check_readiness()
    payload = readiness.to_dict()
    payload["available"] = readiness.ready
    if not readiness.ready and readiness.install_hint:
        payload["note"] = readiness.install_hint
    return payload


@app.post("/asr/transcribe")
def asr_transcribe(payload: AsrTranscribeRequest) -> dict:
    from pathlib import Path as _Path

    from collectors.asr import transcribe_url

    if not payload.url.strip():
        raise HTTPException(status_code=400, detail="'url' is required")

    result = transcribe_url(payload.url, output_dir=_Path(payload.output_dir), language=payload.language)
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error)
    return {
        "url": payload.url,
        "backend": result.backend,
        "audio_path": result.audio_path,
        "text": result.text,
        "char_count": len(result.text),
    }
