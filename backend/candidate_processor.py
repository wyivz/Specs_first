from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from collectors.base import Collector
from collectors.browser import BrowserAuthRequired
from collectors.platform_auth import PlatformAuthRequired
from schemas import ProductAsset, ProductCandidate, TaskEvent, TaskState, to_dict
from schemas.serialize import finding_from_dict, official_spec_from_dict


@dataclass
class CandidateProcessor:
    """Runs Phases 1–4 for a single SKU."""

    collector: Collector
    router: object
    emit: Callable[[str, str, str, TaskState, dict | None], None]

    def process(
        self,
        *,
        task_id: str,
        candidate: ProductCandidate,
        in_progress_payload: dict[str, Any] | None,
        use_browser: bool,
        storage_state_path: str,
    ) -> ProductAsset | None:
        progress = in_progress_payload
        if progress:
            official_specs = [official_spec_from_dict(item) for item in progress["official_specs"]]
            highlights = progress["highlights"]
            findings = [finding_from_dict(item) for item in progress["findings"]]
        else:
            self.emit(
                "phase_started",
                f"Phase 1: collecting official specs for {candidate.sku}",
                TaskState.RUNNING,
                {"sku": candidate.sku, "phase": 1},
            )
            official_specs, highlights = self.collector.collect_official_specs(
                candidate,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
            )
            self.emit(
                "specs_collected",
                f"Official specs collected for {candidate.sku}",
                TaskState.RUNNING,
                {
                    "sku": candidate.sku,
                    "official_specs": [to_dict(spec) for spec in official_specs],
                    "spec_highlights": highlights,
                },
            )

            self.emit(
                "phase_started",
                f"Phase 2: dehydrating field evidence for {candidate.sku}",
                TaskState.RUNNING,
                {"sku": candidate.sku, "phase": 2},
            )
            try:
                corpus = self.collector.collect_real_world_corpus(
                    candidate,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                )
            except PlatformAuthRequired as exc:
                exc.in_progress_payload = {
                    "official_specs": [to_dict(spec) for spec in official_specs],
                    "highlights": highlights,
                    "findings": [],
                }
                raise
            findings = self.router.extract_real_world_findings(candidate.sku, corpus)
            self.emit(
                "findings_extracted",
                f"Extracted {len(findings)} real-world findings for {candidate.sku}",
                TaskState.RUNNING,
                {
                    "sku": candidate.sku,
                    "findings": [to_dict(finding) for finding in findings],
                    "corpus_size": len(corpus),
                },
            )

        self.emit(
            "phase_started",
            f"Phase 3: normalizing price evidence for {candidate.sku}",
            TaskState.RUNNING,
            {"sku": candidate.sku, "phase": 3},
        )
        prices = self._collect_prices(
            task_id=task_id,
            candidate=candidate,
            official_specs=official_specs,
            highlights=highlights,
            findings=findings,
            use_browser=use_browser,
            storage_state_path=storage_state_path,
        )

        self.emit(
            "phase_started",
            f"Phase 4: arbitrating conflicts for {candidate.sku}",
            TaskState.RUNNING,
            {"sku": candidate.sku, "phase": 4},
        )
        warnings = self.router.arbitrate_conflicts(findings, official_specs)
        summary = self.router.summarize(warnings, findings)

        return ProductAsset(
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

    def _collect_prices(
        self,
        *,
        task_id: str,
        candidate: ProductCandidate,
        official_specs: list,
        highlights: list[str],
        findings: list,
        use_browser: bool,
        storage_state_path: str,
    ) -> list:
        try:
            prices = self.collector.collect_prices(
                candidate,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
            )
            return self.router.enrich_prices_with_ocr(candidate.sku, prices)
        except BrowserAuthRequired as exc:
            exc.in_progress_payload = {
                "official_specs": [to_dict(spec) for spec in official_specs],
                "highlights": highlights,
                "findings": [to_dict(finding) for finding in findings],
            }
            raise
        except PlatformAuthRequired as exc:
            # Soft-degrade: Taobao mtop/session failures should not freeze the whole run.
            if hasattr(self.collector, "diagnostics"):
                self.collector.diagnostics.record(
                    "price",
                    f"price stage soft-skip auth for {candidate.sku}: {exc}",
                    level="warning",
                    sku=candidate.sku,
                )
            self.emit(
                "price_degraded",
                f"Price auth soft-skipped for {candidate.sku}; continuing with available evidence",
                TaskState.RUNNING,
                {"sku": candidate.sku, "error": str(exc)},
            )
            return []
        except Exception as exc:
            if hasattr(self.collector, "diagnostics"):
                self.collector.diagnostics.record(
                    "price",
                    f"price stage downgraded for {candidate.sku}: {exc}",
                    level="warning",
                    sku=candidate.sku,
                )
            self.emit(
                "price_degraded",
                f"Price stage downgraded for {candidate.sku}; continuing with specs/findings only",
                TaskState.RUNNING,
                {"sku": candidate.sku, "error": str(exc)},
            )
            return []
