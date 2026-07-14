from __future__ import annotations

from typing import Any

from backend.task_runner import task_manager
from schemas import to_dict

__all__ = [
    "events_since",
    "get_task_result",
    "get_task_status",
]


def get_task_status(task_id: str) -> dict[str, Any]:
    """In-process task status — no TestClient / JSON round-trip."""
    record = task_manager.get(task_id)
    if not record:
        raise KeyError(f"Task not found: {task_id}")
    return {"task_id": task_id, "state": record.state, "error": record.error or ""}


def events_since(task_id: str, seen: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Return only new events for ``task_id`` after index ``seen``.

    Serializes the delta only, not the full history, so live polls stay cheap
    as the event log grows.
    """
    if seen < 0:
        seen = 0
    matched = [event for event in task_manager.event_bus.events if event.task_id == task_id]
    total = len(matched)
    if seen > total:
        seen = 0
    return [to_dict(event) for event in matched[seen:]], total


def get_task_result(task_id: str) -> dict[str, Any]:
    """In-process result payload matching ``GET /tasks/{id}/result``."""
    record = task_manager.get(task_id)
    if not record:
        raise KeyError(f"Task not found: {task_id}")
    if not record.result:
        return {"task_id": task_id, "state": record.state, "error": record.error or ""}
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
