from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.checkpoint import CheckpointStore, TaskCheckpoint, create_checkpoint_store
from backend.events import InMemoryEventBus
from backend.model_router import create_model_router
from collectors import MockCollector, RealCollector
from collectors.base import Collector
from collectors.browser import BrowserAuthRequired
from collectors.platform_auth import PlatformAuthRequired
from obsidian import MatrixCsvExporter, ObsidianWriter
from schemas import (
    ComparisonMatrix,
    ProductAsset,
    ProductCandidate,
    TaskEvent,
    TaskState,
    to_dict,
)
from schemas.matrix import build_comparison_matrix, build_partial_row
from schemas.serialize import asset_from_dict, candidate_from_dict
from backend.candidate_processor import CandidateProcessor


@dataclass
class TaskResult:
    task_id: str
    candidates: list[ProductCandidate]
    selected_candidates: list[ProductCandidate]
    assets: list[ProductAsset]
    matrix: ComparisonMatrix
    output_paths: list[Path]
    events: list[TaskEvent]
    state: TaskState
    diagnostics: list[dict] = field(default_factory=list)


@dataclass
class SpecsFirstPipeline:
    collector: Collector = field(default_factory=MockCollector)
    router: object = field(default_factory=create_model_router)
    event_bus: InMemoryEventBus = field(default_factory=InMemoryEventBus)
    vault_path: Path = Path("vault_output")
    checkpoint_store: CheckpointStore = field(default_factory=create_checkpoint_store)

    def run(
        self,
        query: str = "",
        category: str = "Product",
        selected_skus: list[str] | None = None,
        source_urls: list[str] | None = None,
        task_id: str | None = None,
        discover_only: bool = False,
        on_event: Callable[[TaskEvent], None] | None = None,
        checkpoint: TaskCheckpoint | None = None,
        use_browser: bool = False,
    ) -> TaskResult:
        if checkpoint:
            return self._run_from_checkpoint(checkpoint, on_event=on_event, use_browser=use_browser)

        task_id = task_id or str(uuid4())
        self._emit(task_id, "phase_started", "Phase 0: discovering candidate SKUs", TaskState.RUNNING, on_event=on_event)
        candidates = self.collector.discover_candidates(query, category)[:10]
        selected = self._select_candidates(candidates, selected_skus)
        self._emit(
            task_id,
            "candidate_found",
            f"Discovered {len(candidates)} candidates; selected {len(selected)} for comparison",
            TaskState.RUNNING,
            {
                "candidates": [to_dict(candidate) for candidate in candidates],
                "selected_skus": [candidate.sku for candidate in selected],
            },
            on_event,
        )

        if discover_only:
            self._emit(
                task_id,
                "task_done",
                "Candidate discovery finished",
                TaskState.DONE,
                {"candidates": [to_dict(candidate) for candidate in candidates]},
                on_event,
            )
            events = list(self.event_bus.stream(task_id))
            empty_matrix = build_comparison_matrix([])
            return TaskResult(task_id, candidates, selected, [], empty_matrix, [], events, TaskState.DONE, [])

        return self._process_candidates(
            task_id=task_id,
            query=query,
            category=category,
            mode="mock" if isinstance(self.collector, MockCollector) else "real",
            source_urls=source_urls or [],
            selected_skus=selected_skus or [candidate.sku for candidate in selected],
            candidates=candidates,
            selected=selected,
            assets=[],
            start_index=0,
            on_event=on_event,
            use_browser=use_browser,
        )

    def _run_from_checkpoint(
        self,
        checkpoint: TaskCheckpoint,
        on_event: Callable[[TaskEvent], None] | None = None,
        use_browser: bool = False,
    ) -> TaskResult:
        task_id = checkpoint.task_id
        self._emit(
            task_id,
            "task_resumed",
            f"Resuming task from checkpoint at SKU index {checkpoint.next_candidate_index}",
            TaskState.RUNNING,
            {"storage_state_path": checkpoint.storage_state_path},
            on_event,
        )
        candidates = [candidate_from_dict(item) for item in checkpoint.candidate_payloads]
        selected = self._select_candidates(candidates, checkpoint.selected_skus or None)
        assets = [asset_from_dict(item) for item in checkpoint.asset_payloads]
        return self._process_candidates(
            task_id=task_id,
            query=checkpoint.query,
            category=checkpoint.category,
            mode=checkpoint.mode,
            source_urls=checkpoint.source_urls,
            selected_skus=checkpoint.selected_skus,
            candidates=candidates,
            selected=selected,
            assets=assets,
            start_index=checkpoint.next_candidate_index,
            in_progress_payload=checkpoint.in_progress_payload,
            on_event=on_event,
            use_browser=use_browser,
            storage_state_path=checkpoint.storage_state_path,
        )

    def _process_candidates(
        self,
        *,
        task_id: str,
        query: str,
        category: str,
        mode: str,
        source_urls: list[str],
        selected_skus: list[str],
        candidates: list[ProductCandidate],
        selected: list[ProductCandidate],
        assets: list[ProductAsset],
        start_index: int,
        in_progress_payload: dict[str, Any] | None = None,
        on_event: Callable[[TaskEvent], None] | None = None,
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> TaskResult:
        partial_rows = [build_partial_row(asset) for asset in assets]

        for index in range(start_index, len(selected)):
            candidate = selected[index]
            try:
                asset = self._process_single_candidate(
                    task_id=task_id,
                    candidate=candidate,
                    index=index,
                    in_progress_payload=in_progress_payload if index == start_index else None,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                    on_event=on_event,
                )
            except BrowserAuthRequired as exc:
                return self._pause_for_auth(
                    task_id=task_id,
                    query=query,
                    category=category,
                    mode=mode,
                    source_urls=source_urls,
                    selected_skus=selected_skus,
                    candidates=candidates,
                    selected=selected,
                    assets=assets,
                    index=index,
                    candidate=candidate,
                    exc=exc,
                    storage_state_path=storage_state_path,
                    on_event=on_event,
                    in_progress_payload=in_progress_payload if index == start_index else None,
                )
            except PlatformAuthRequired as exc:
                auth_exc = BrowserAuthRequired(
                    exc.message,
                    url=exc.url,
                    storage_state_path=Path(exc.storage_state_path) if exc.storage_state_path else None,
                )
                if exc.in_progress_payload:
                    auth_exc.in_progress_payload = exc.in_progress_payload
                return self._pause_for_auth(
                    task_id=task_id,
                    query=query,
                    category=category,
                    mode=mode,
                    source_urls=source_urls,
                    selected_skus=selected_skus,
                    candidates=candidates,
                    selected=selected,
                    assets=assets,
                    index=index,
                    candidate=candidate,
                    exc=auth_exc,
                    storage_state_path=storage_state_path,
                    on_event=on_event,
                    in_progress_payload=in_progress_payload if index == start_index else None,
                )
            except Exception as exc:
                self._record_sku_failure(task_id, candidate.sku, exc, on_event)
                continue

            if asset is None:
                continue
            assets.append(asset)
            partial_rows.append(build_partial_row(asset))
            self._emit(
                task_id,
                "matrix_row_updated",
                f"Matrix row ready for {candidate.sku}",
                TaskState.RUNNING,
                {"matrix_rows": partial_rows, "sku": candidate.sku},
                on_event,
            )
            self._emit(
                task_id,
                "diagnostics_updated",
                f"Collector diagnostics updated for {candidate.sku}",
                TaskState.RUNNING,
                {"records": self._collector_diagnostics()},
                on_event,
            )

        matrix = build_comparison_matrix(assets)
        self._emit(task_id, "phase_started", "Writing Obsidian assets", TaskState.RUNNING, on_event=on_event)
        output_paths = ObsidianWriter(self.vault_path).write(category, assets, matrix)
        output_paths.append(MatrixCsvExporter(self.vault_path).write(category, matrix))
        self.checkpoint_store.delete(task_id)
        diagnostics = self._collector_diagnostics()
        self._emit(
            task_id,
            "task_done",
            "Comparison matrix and Obsidian assets are ready",
            TaskState.DONE,
            {
                "output_paths": [str(path) for path in output_paths],
                "matrix": {
                    "columns": [to_dict(column) for column in matrix.columns],
                    "rows": [to_dict(row) for row in matrix.rows],
                },
                "diagnostics": diagnostics,
            },
            on_event,
        )
        events = list(self.event_bus.stream(task_id))
        return TaskResult(
            task_id,
            candidates,
            selected,
            assets,
            matrix,
            output_paths,
            events,
            TaskState.DONE,
            diagnostics,
        )

    def _process_single_candidate(
        self,
        *,
        task_id: str,
        candidate: ProductCandidate,
        index: int,
        in_progress_payload: dict[str, Any] | None,
        use_browser: bool,
        storage_state_path: str,
        on_event: Callable[[TaskEvent], None] | None,
    ) -> ProductAsset | None:
        processor = CandidateProcessor(
            collector=self.collector,
            router=self.router,
            emit=lambda event_type, message, state, payload=None: self._emit(
                task_id, event_type, message, state, payload, on_event
            ),
        )
        return processor.process(
            task_id=task_id,
            candidate=candidate,
            in_progress_payload=in_progress_payload,
            use_browser=use_browser,
            storage_state_path=storage_state_path,
        )

    def _pause_for_auth(
        self,
        *,
        task_id: str,
        query: str,
        category: str,
        mode: str,
        source_urls: list[str],
        selected_skus: list[str],
        candidates: list[ProductCandidate],
        selected: list[ProductCandidate],
        assets: list[ProductAsset],
        index: int,
        candidate: ProductCandidate,
        exc: BrowserAuthRequired,
        storage_state_path: str,
        on_event: Callable[[TaskEvent], None] | None,
        in_progress_payload: dict[str, Any] | None,
    ) -> TaskResult:
        checkpoint = TaskCheckpoint(
            task_id=task_id,
            query=query,
            category=category,
            mode=mode,
            vault_path=str(self.vault_path),
            source_urls=source_urls,
            selected_skus=selected_skus,
            candidate_payloads=[to_dict(item) for item in candidates],
            asset_payloads=[to_dict(item) for item in assets],
            next_candidate_index=index,
            pause_reason=str(exc),
            pause_url=exc.url,
            storage_state_path=str(exc.storage_state_path or storage_state_path or ""),
            state=TaskState.PAUSED_NEED_AUTH,
        )
        if getattr(exc, "in_progress_payload", None):
            checkpoint.in_progress_payload = exc.in_progress_payload
        elif in_progress_payload:
            checkpoint.in_progress_payload = in_progress_payload
        self.checkpoint_store.save(checkpoint)
        self._emit(
            task_id,
            "auth_required",
            f"Authentication required before continuing for {candidate.sku}",
            TaskState.PAUSED_NEED_AUTH,
            {
                "sku": candidate.sku,
                "pause_url": exc.url,
                "pause_reason": str(exc),
                "storage_state_path": checkpoint.storage_state_path,
            },
            on_event,
        )
        events = list(self.event_bus.stream(task_id))
        matrix = build_comparison_matrix(assets)
        return TaskResult(
            task_id,
            candidates,
            selected,
            assets,
            matrix,
            [],
            events,
            TaskState.PAUSED_NEED_AUTH,
            self._collector_diagnostics(),
        )

    def _record_sku_failure(
        self,
        task_id: str,
        sku: str,
        exc: Exception,
        on_event: Callable[[TaskEvent], None] | None,
    ) -> None:
        message = f"{sku} failed: {exc}"
        if hasattr(self.collector, "diagnostics"):
            self.collector.diagnostics.record("pipeline", message, level="error", sku=sku)
        self._emit(
            task_id,
            "sku_failed",
            message,
            TaskState.RUNNING,
            {"sku": sku, "error": str(exc), "records": self._collector_diagnostics()},
            on_event,
        )

    def _collector_diagnostics(self) -> list[dict]:
        if hasattr(self.collector, "diagnostics_report"):
            return self.collector.diagnostics_report()
        if hasattr(self.collector, "diagnostics"):
            return self.collector.diagnostics.to_dicts()
        return []

    def _select_candidates(
        self,
        candidates: list[ProductCandidate],
        selected_skus: list[str] | None,
    ) -> list[ProductCandidate]:
        if not selected_skus:
            return candidates[:3]

        wanted = {sku for sku in selected_skus if sku.strip()}
        exact = [candidate for candidate in candidates if candidate.sku in wanted]
        if exact:
            return _dedupe_candidates(exact)

        # Exact after normalization (e.g. "SEL50F12GM" == "sel50f12gm").
        wanted_norm = [self._normalize_sku(sku) for sku in wanted]
        wanted_norm_set = {item for item in wanted_norm if item}
        normalized_exact = [
            candidate
            for candidate in candidates
            if self._normalize_sku(candidate.sku) in wanted_norm_set
        ]
        if normalized_exact:
            return _dedupe_candidates(normalized_exact)

        # Live search titles drift (e.g. "f/1.2" vs "F1.2"); allow normalized contains,
        # but prefer shorter titles so model codes beat long marketplace headlines.
        fuzzy: list[ProductCandidate] = []
        for candidate in candidates:
            key = self._normalize_sku(candidate.sku)
            if any(
                (needle and len(needle) >= 8 and (needle in key or key in needle))
                for needle in wanted_norm
            ):
                fuzzy.append(candidate)
        if fuzzy:
            fuzzy.sort(key=lambda item: len(item.sku))
            return _dedupe_candidates(fuzzy)[:3]

        # Prefer stable model codes when the preferred marketing title was not found.
        code_hits = [
            candidate
            for candidate in candidates
            if any(
                token.isascii() and token.isalnum() and len(token) >= 6 and token.upper() in candidate.sku.upper()
                for sku in wanted
                for token in sku.replace("/", " ").split()
            )
        ]
        if code_hits:
            return _dedupe_candidates(code_hits)[:3]

        # Last resort: keep a compact model-code SKU label when search titles drifted.
        compact = [sku for sku in wanted if sku.replace("-", "").isalnum() and 6 <= len(sku) <= 32]
        if compact and candidates:
            base = candidates[0]
            return [
                ProductCandidate(
                    sku=compact[0],
                    brand=base.brand,
                    category=base.category,
                    source_url=base.source_url,
                    confidence=max(0.4, base.confidence * 0.9),
                )
            ]
        return candidates[:1]

    @staticmethod
    def _normalize_sku(value: str) -> str:
        return "".join(ch for ch in value.casefold() if ch.isalnum())

    def _emit(
        self,
        task_id: str,
        event_type: str,
        message: str,
        state: TaskState,
        payload: dict | None = None,
        on_event: Callable[[TaskEvent], None] | None = None,
    ) -> None:
        event = TaskEvent(task_id, event_type, message, state, payload or {})
        self.event_bus.publish(event)
        if on_event:
            on_event(event)


def _dedupe_candidates(candidates: list[ProductCandidate]) -> list[ProductCandidate]:
    seen: set[str] = set()
    unique: list[ProductCandidate] = []
    for candidate in candidates:
        key = candidate.sku.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def run_mock_demo() -> TaskResult:
    return SpecsFirstPipeline().run("Zeiss 50mm 镜头", "Lens")


def create_pipeline(
    mode: str = "mock",
    source_urls: list[str] | None = None,
    vault_path: str | Path = "vault_output",
    model_mode: str | None = None,
    event_bus: InMemoryEventBus | None = None,
    checkpoint_store: CheckpointStore | None = None,
) -> SpecsFirstPipeline:
    collector = RealCollector(source_urls=source_urls or []) if mode == "real" else MockCollector()
    return SpecsFirstPipeline(
        collector=collector,
        router=create_model_router(model_mode),
        event_bus=event_bus or InMemoryEventBus(),
        vault_path=Path(vault_path),
        checkpoint_store=checkpoint_store or create_checkpoint_store(),
    )


if __name__ == "__main__":
    result = run_mock_demo()
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "state": result.state.value,
                "rows": result.matrix.to_plain_rows(),
                "output_paths": [str(path) for path in result.output_paths],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
