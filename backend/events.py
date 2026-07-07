from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

from schemas import TaskEvent, TaskState


@dataclass
class InMemoryEventBus:
    events: list[TaskEvent] = field(default_factory=list)
    _subscribers: dict[str, list[queue.Queue[TaskEvent | None]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, event: TaskEvent) -> None:
        with self._lock:
            self.events.append(event)
            subscribers = list(self._subscribers.get(event.task_id, []))
        for subscriber in subscribers:
            subscriber.put(event)

    def stream(self, task_id: str) -> Iterator[TaskEvent]:
        for event in self.events:
            if event.task_id == task_id:
                yield event

    def subscribe(self, task_id: str, replay: bool = True) -> Iterator[TaskEvent]:
        subscriber: queue.Queue[TaskEvent | None] = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(task_id, []).append(subscriber)
            history = [event for event in self.events if event.task_id == task_id] if replay else []
        try:
            for event in history:
                yield event
            while True:
                event = subscriber.get()
                if event is None:
                    break
                yield event
                if event.event_type in {"task_done", "task_failed"} or event.state in {TaskState.DONE, TaskState.FAILED}:
                    break
        finally:
            with self._lock:
                task_subscribers = self._subscribers.get(task_id, [])
                if subscriber in task_subscribers:
                    task_subscribers.remove(subscriber)

    def close(self, task_id: str) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(task_id, []))
        for subscriber in subscribers:
            subscriber.put(None)
