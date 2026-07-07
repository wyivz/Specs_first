from __future__ import annotations

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
        category=payload.get("category", "Lens"),
        mode=payload.get("mode", "mock"),
        source_urls=payload.get("source_urls", []),
    )
    return {"candidates": candidates}


@app.post("/tasks")
def create_task(payload: dict) -> dict:
    task_id = task_manager.start_task(
        query=payload.get("query", ""),
        category=payload.get("category", "Lens"),
        selected_skus=payload.get("selected_skus"),
        source_urls=payload.get("source_urls", []),
        mode=payload.get("mode", "mock"),
        vault_path=payload.get("vault_path", "vault_output"),
        discover_only=payload.get("discover_only", False),
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
    }
