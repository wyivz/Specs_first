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
from obsidian import ObsidianWriter
from schemas import (
    ComparisonMatrix,
    ProductAsset,
    ProductCandidate,
    TaskEvent,
    TaskState,
    to_dict,
)
from schemas.matrix import build_comparison_matrix, build_partial_row
from schemas.serialize import asset_from_dict, candidate_from_dict, finding_from_dict, official_spec_from_dict


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
        category: str = "Lens",
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
            return TaskResult(task_id, candidates, selected, [], empty_matrix, [], events, TaskState.DONE)

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
            progress = in_progress_payload if index == start_index and in_progress_payload else None

            if progress:
                official_specs = [official_spec_from_dict(item) for item in progress["official_specs"]]
                highlights = progress["highlights"]
                findings = [finding_from_dict(item) for item in progress["findings"]]
            else:
                self._emit(
                    task_id,
                    "phase_started",
                    f"Phase 1: collecting official specs for {candidate.sku}",
                    TaskState.RUNNING,
                    {"sku": candidate.sku, "phase": 1},
                    on_event,
                )
                official_specs, highlights = self.collector.collect_official_specs(candidate)
                self._emit(
                    task_id,
                    "specs_collected",
                    f"Official specs collected for {candidate.sku}",
                    TaskState.RUNNING,
                    {
                        "sku": candidate.sku,
                        "official_specs": [to_dict(spec) for spec in official_specs],
                        "spec_highlights": highlights,
                    },
                    on_event,
                )

                self._emit(
                    task_id,
                    "phase_started",
                    f"Phase 2: dehydrating field evidence for {candidate.sku}",
                    TaskState.RUNNING,
                    {"sku": candidate.sku, "phase": 2},
                    on_event,
                )
                corpus = self.collector.collect_real_world_corpus(candidate)
                findings = self.router.extract_real_world_findings(candidate.sku, corpus)
                self._emit(
                    task_id,
                    "findings_extracted",
                    f"Extracted {len(findings)} real-world findings for {candidate.sku}",
                    TaskState.RUNNING,
                    {
                        "sku": candidate.sku,
                        "findings": [to_dict(finding) for finding in findings],
                        "corpus_size": len(corpus),
                    },
                    on_event,
                )

            self._emit(
                task_id,
                "phase_started",
                f"Phase 3: normalizing price evidence for {candidate.sku}",
                TaskState.RUNNING,
                {"sku": candidate.sku, "phase": 3},
                on_event,
            )
            try:
                prices = self.collector.collect_prices(
                    candidate,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                )
                prices = self.router.enrich_prices_with_ocr(candidate.sku, prices)
            except BrowserAuthRequired as exc:
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
                checkpoint.in_progress_payload = {
                    "official_specs": [to_dict(spec) for spec in official_specs],
                    "highlights": highlights,
                    "findings": [to_dict(finding) for finding in findings],
                }
                self.checkpoint_store.save(checkpoint)
                self._emit(
                    task_id,
                    "auth_required",
                    f"Authentication required before continuing price capture for {candidate.sku}",
                    TaskState.PAUSED_NEED_AUTH,
                    {
                        "sku": candidate.sku,
                        "pause_url": exc.url,
                        "storage_state_path": checkpoint.storage_state_path,
                    },
                    on_event,
                )
                events = list(self.event_bus.stream(task_id))
                matrix = build_comparison_matrix(assets)
                return TaskResult(task_id, candidates, selected, assets, matrix, [], events, TaskState.PAUSED_NEED_AUTH)

            self._emit(
                task_id,
                "phase_started",
                f"Phase 4: arbitrating conflicts for {candidate.sku}",
                TaskState.RUNNING,
                {"sku": candidate.sku, "phase": 4},
                on_event,
            )
            warnings = self.router.arbitrate_conflicts(findings, official_specs)
            summary = self.router.summarize(warnings, findings)

            asset = ProductAsset(
                sku=candidate.sku,
                brand=candidate.brand,
                category=candidate.category,
                official_specs=official_specs,
                spec_highlights=highlights,
                real_world_findings=findings,
                prices=prices,
                conflict_warnings=warnings,
                arbitration_summary=summary,
            )
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

        matrix = build_comparison_matrix(assets)
        self._emit(task_id, "phase_started", "Writing Obsidian assets", TaskState.RUNNING, on_event=on_event)
        output_paths = ObsidianWriter(self.vault_path).write(category, assets, matrix)
        self.checkpoint_store.delete(task_id)
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
            },
            on_event,
        )
        events = list(self.event_bus.stream(task_id))
        return TaskResult(task_id, candidates, selected, assets, matrix, output_paths, events, TaskState.DONE)

    def _select_candidates(
        self,
        candidates: list[ProductCandidate],
        selected_skus: list[str] | None,
    ) -> list[ProductCandidate]:
        if selected_skus:
            wanted = set(selected_skus)
            return [candidate for candidate in candidates if candidate.sku in wanted]
        return candidates[:3]

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
