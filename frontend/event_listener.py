from __future__ import annotations

import queue
import threading
from typing import Any

from backend.task_runner import task_manager
from schemas import to_dict


_registry: dict[str, queue.Queue[dict[str, Any] | None]] = {}
_threads: dict[str, threading.Thread] = {}


def _listen(task_id: str, event_queue: queue.Queue[dict[str, Any] | None]) -> None:
    try:
        for event in task_manager.event_bus.subscribe(task_id):
            event_queue.put(to_dict(event))
    finally:
        event_queue.put(None)


def is_listener_active(task_id: str) -> bool:
    thread = _threads.get(task_id)
    return thread is not None and thread.is_alive()


def ensure_listener(task_id: str) -> None:
    if is_listener_active(task_id):
        return
    start_listener(task_id)


def start_listener(task_id: str) -> None:
    stop_listener(task_id)
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    _registry[task_id] = event_queue
    thread = threading.Thread(target=_listen, args=(task_id, event_queue), daemon=True)
    _threads[task_id] = thread
    thread.start()


def stop_listener(task_id: str) -> None:
    thread = _threads.pop(task_id, None)
    _registry.pop(task_id, None)
    if thread and thread.is_alive():
        task_manager.event_bus.close(task_id)


def drain_events(task_id: str) -> list[dict[str, Any]]:
    event_queue = _registry.get(task_id)
    if not event_queue:
        return []

    events: list[dict[str, Any]] = []
    while True:
        try:
            item = event_queue.get_nowait()
        except queue.Empty:
            break
        if item is None:
            stop_listener(task_id)
            break
        events.append(item)
    return events


def sync_events_from_snapshot(task_id: str, snapshot_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import streamlit as st

    seen = st.session_state.get("seen_event_count", 0)
    new_events = snapshot_events[seen:]
    st.session_state["seen_event_count"] = len(snapshot_events)
    return new_events
