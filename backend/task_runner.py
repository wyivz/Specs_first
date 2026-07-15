from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from backend.checkpoint import CheckpointStore, create_checkpoint_store
from backend.events import InMemoryEventBus
from backend.model_router import create_model_router
from backend.pipeline import SpecsFirstPipeline, TaskResult
from collectors.base import Collector
from schemas import TaskState


@dataclass
class TaskRecord:
    task_id: str
    state: str = "PENDING"
    result: TaskResult | None = None
    error: str = ""
    thread: threading.Thread | None = None


@dataclass
class TaskManager:
    event_bus: InMemoryEventBus = field(default_factory=InMemoryEventBus)
    checkpoint_store: CheckpointStore = field(default_factory=create_checkpoint_store)
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create_pipeline(
        self,
        mode: str = "mock",
        source_urls: list[str] | None = None,
        vault_path: str | Path = "vault_output",
        model_mode: str | None = None,
    ) -> SpecsFirstPipeline:
        # Lazy imports: collectors.real pulls Playwright/adapters and slows GUI cold start.
        from collectors import MockCollector, RealCollector

        resolved_router_mode = model_mode or ("keyword" if mode == "mock" else None)
        router = create_model_router(resolved_router_mode)
        if mode == "real":
            collector: Collector = RealCollector(source_urls=source_urls or [], router=router)
        else:
            collector = MockCollector()
        return SpecsFirstPipeline(
            collector=collector,
            router=router,
            event_bus=self.event_bus,
            vault_path=Path(vault_path),
            checkpoint_store=self.checkpoint_store,
        )

    def start_task(
        self,
        query: str,
        category: str = "Product",
        selected_skus: list[str] | None = None,
        source_urls: list[str] | None = None,
        mode: str = "mock",
        vault_path: str | Path = "vault_output",
        discover_only: bool = False,
        task_id: str | None = None,
        use_browser: bool = False,
    ) -> str:
        task_id = task_id or str(uuid4())
        record = TaskRecord(task_id=task_id, state="RUNNING")
        with self._lock:
            self.tasks[task_id] = record

        def runner() -> None:
            try:
                pipeline = self.create_pipeline(mode=mode, source_urls=source_urls, vault_path=vault_path)
                result = pipeline.run(
                    query=query,
                    category=category,
                    selected_skus=selected_skus,
                    source_urls=source_urls,
                    task_id=task_id,
                    discover_only=discover_only,
                    use_browser=use_browser,
                )
                record.result = result
                record.state = result.state.value
            except Exception as exc:
                record.state = TaskState.FAILED.value
                record.error = str(exc)
                from schemas import TaskEvent

                self.event_bus.publish(
                    TaskEvent(task_id, "task_failed", record.error, TaskState.FAILED, {"error": record.error})
                )
            finally:
                if record.state != TaskState.PAUSED_NEED_AUTH.value:
                    self.event_bus.close(task_id)

        thread = threading.Thread(target=runner, daemon=True)
        record.thread = thread
        thread.start()
        return task_id

    def resume_task(self, task_id: str, use_browser: bool = True) -> str:
        checkpoint = self.checkpoint_store.load(task_id)
        if not checkpoint:
            raise KeyError(f"No checkpoint found for task {task_id}")

        record = self.tasks.get(task_id) or TaskRecord(task_id=task_id)
        record.state = "RUNNING"
        with self._lock:
            self.tasks[task_id] = record

        def runner() -> None:
            try:
                pipeline = self.create_pipeline(
                    mode=checkpoint.mode,
                    source_urls=checkpoint.source_urls,
                    vault_path=checkpoint.vault_path,
                )
                result = pipeline.run(checkpoint=checkpoint, use_browser=use_browser)
                record.result = result
                record.state = result.state.value
            except Exception as exc:
                record.state = TaskState.FAILED.value
                record.error = str(exc)
                from schemas import TaskEvent

                self.event_bus.publish(
                    TaskEvent(task_id, "task_failed", record.error, TaskState.FAILED, {"error": record.error})
                )
            finally:
                if record.state != TaskState.PAUSED_NEED_AUTH.value:
                    self.event_bus.close(task_id)

        thread = threading.Thread(target=runner, daemon=True)
        record.thread = thread
        thread.start()
        return task_id

    def get(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def discover(
        self,
        query: str,
        category: str = "Product",
        mode: str = "mock",
        source_urls: list[str] | None = None,
        *,
        quick: bool = False,
        on_progress=None,
    ) -> list[dict]:
        pipeline = self.create_pipeline(mode=mode, source_urls=source_urls)
        discover_kwargs: dict = {"quick": quick, "on_progress": on_progress}
        try:
            candidates = list(
                pipeline.collector.discover_candidates(query, category, **discover_kwargs)[:10]
            )
        except TypeError:
            # MockCollector / older collectors may not accept quick flags.
            candidates = list(pipeline.collector.discover_candidates(query, category)[:10])

        from schemas import to_dict

        return [to_dict(candidate) for candidate in candidates[:10]]


task_manager = TaskManager()
