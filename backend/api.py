from __future__ import annotations

import base64
import json

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the API: fastapi uvicorn") from exc

from backend.task_runner import task_manager
from schemas import to_dict

app = FastAPI(title="Specs-First", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "specs-first"}


@app.post("/discover")
def discover(payload: dict) -> dict:
    candidates = task_manager.discover(
        query=payload.get("query", ""),
        category=payload.get("category", "Product"),
        mode=payload.get("mode", "mock"),
        source_urls=payload.get("source_urls", []),
    )
    return {"candidates": candidates}


@app.post("/tasks")
def create_task(payload: dict) -> dict:
    task_id = task_manager.start_task(
        query=payload.get("query", ""),
        category=payload.get("category", "Product"),
        selected_skus=payload.get("selected_skus"),
        source_urls=payload.get("source_urls", []),
        mode=payload.get("mode", "mock"),
        vault_path=payload.get("vault_path", "vault_output"),
        discover_only=payload.get("discover_only", False),
        use_browser=payload.get("use_browser", False),
    )
    return {"task_id": task_id, "state": "RUNNING"}


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "state": record.state, "error": record.error}


@app.get("/tasks/{task_id}/events")
def stream_events(task_id: str) -> StreamingResponse:
    record = task_manager.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")

    def event_stream():
        for event in task_manager.event_bus.subscribe(task_id):
            yield f"data: {json.dumps(to_dict(event), ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
def resume_auth(task_id: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    try:
        task_manager.resume_task(task_id, use_browser=payload.get("use_browser", True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"task_id": task_id, "state": "RUNNING"}


@app.get("/tasks/{task_id}/checkpoint")
def get_checkpoint(task_id: str) -> dict:
    checkpoint = task_manager.checkpoint_store.load(task_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return to_dict(checkpoint)


# ---------------------------------------------------------------------------
# Embedded browser control (Milestone 2 tail item) — lets a captcha that
# triggers PlaywrightCapture's headed fallback be solved from inside the
# web UI (screenshot + click/type relay) instead of a separate OS window.
# ---------------------------------------------------------------------------

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
def browser_command(task_id: str, payload: dict) -> dict:
    from collectors.embedded_browser import get_bridge

    bridge = get_bridge(task_id)
    if not bridge:
        raise HTTPException(status_code=404, detail="No active embedded browser session for this task")
    action = payload.get("action", "")
    if action not in {"click", "type", "key", "scroll"}:
        raise HTTPException(status_code=400, detail="action must be one of click/type/key/scroll")
    kwargs = {key: value for key, value in payload.items() if key != "action"}
    bridge.submit_command(action, **kwargs)
    return {"task_id": task_id, "queued": action}


# ---------------------------------------------------------------------------
# ASR endpoints — manual-trigger only (P3)
# ---------------------------------------------------------------------------

@app.get("/asr/status")
def asr_status() -> dict:
    """Return which local ASR backend is available (if any)."""
    from collectors.asr import available_backend

    backend = available_backend()
    return {
        "available": backend is not None,
        "backend": backend or "none",
        "note": (
            "Install 'funasr' for SenseVoice (recommended for Chinese) or "
            "'faster-whisper' for multilingual Whisper support."
            if backend is None
            else ""
        ),
    }


@app.post("/asr/transcribe")
def asr_transcribe(payload: dict) -> dict:
    """Transcribe audio from a video URL using the local ASR backend.

    Body: { "url": "https://...", "language": "auto" | "zh" | "en" }

    This endpoint is intentionally *not* wired into the default pipeline.
    It is designed for manual, on-demand use when a video has no CC subtitles.
    Estimated runtime: CPU ~10-30 min per hour of audio; GPU much faster.
    """
    from pathlib import Path as _Path

    from collectors.asr import transcribe_url

    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="'url' is required")
    language = payload.get("language", "auto")
    output_dir_str = payload.get("output_dir", "vault_output/asr_cache")

    result = transcribe_url(url, output_dir=_Path(output_dir_str), language=language)
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error)
    return {
        "url": url,
        "backend": result.backend,
        "audio_path": result.audio_path,
        "text": result.text,
        "char_count": len(result.text),
    }
