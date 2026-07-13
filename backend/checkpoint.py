from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.config import settings
from schemas import TaskState


@dataclass
class TaskCheckpoint:
    task_id: str
    query: str
    category: str
    mode: str
    vault_path: str
    source_urls: list[str] = field(default_factory=list)
    selected_skus: list[str] = field(default_factory=list)
    candidate_payloads: list[dict[str, Any]] = field(default_factory=list)
    asset_payloads: list[dict[str, Any]] = field(default_factory=list)
    next_candidate_index: int = 0
    pause_reason: str = ""
    pause_url: str = ""
    storage_state_path: str = ""
    in_progress_payload: dict[str, Any] | None = None
    category_profile: dict[str, Any] | None = None
    state: TaskState = TaskState.PAUSED_NEED_AUTH
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class CheckpointStore(Protocol):
    def save(self, checkpoint: TaskCheckpoint) -> None: ...

    def load(self, task_id: str) -> TaskCheckpoint | None: ...

    def delete(self, task_id: str) -> None: ...


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self._items: dict[str, TaskCheckpoint] = {}

    def save(self, checkpoint: TaskCheckpoint) -> None:
        checkpoint.updated_at = datetime.now(UTC).isoformat()
        self._items[checkpoint.task_id] = checkpoint

    def load(self, task_id: str) -> TaskCheckpoint | None:
        return self._items.get(task_id)

    def delete(self, task_id: str) -> None:
        self._items.pop(task_id, None)


class RedisCheckpointStore:
    def __init__(self, redis_url: str) -> None:
        import redis

        self.client = redis.from_url(redis_url, decode_responses=True)
        self.prefix = "specs-first:checkpoint:"

    def save(self, checkpoint: TaskCheckpoint) -> None:
        checkpoint.updated_at = datetime.now(UTC).isoformat()
        payload = json.dumps(asdict(checkpoint), ensure_ascii=False)
        self.client.set(self.prefix + checkpoint.task_id, payload)

    def load(self, task_id: str) -> TaskCheckpoint | None:
        payload = self.client.get(self.prefix + task_id)
        if not payload:
            return None
        data = json.loads(payload)
        data["state"] = TaskState(data["state"])
        return TaskCheckpoint(**data)

    def delete(self, task_id: str) -> None:
        self.client.delete(self.prefix + task_id)


def create_checkpoint_store() -> CheckpointStore:
    if settings.redis_url:
        try:
            store = RedisCheckpointStore(settings.redis_url)
            store.client.ping()
            return store
        except Exception:
            pass
    return MemoryCheckpointStore()
