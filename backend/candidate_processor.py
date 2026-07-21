from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.live_progress import emit_live_step, now_iso
from collectors.base import Collector
from collectors.browser import BrowserAuthRequired
from collectors.platform_auth import PlatformAuthRequired
from schemas import OfficialSpec, ProductAsset, ProductCandidate, TaskState, to_dict
from schemas.category_profile import DynamicCategoryProfile, canonical_slots, map_spec_name_to_slot
from schemas.serialize import finding_from_dict, official_spec_from_dict


PHASE_LABELS: dict[int, str] = {
    1: "采集官方规格",
    2: "采集真实口碑",
    3: "采集到手价",
    4: "冲突仲裁",
}


@dataclass
class CandidateProcessor:
    """Runs Phases 1–4 for a single SKU."""

    collector: Collector
    router: object
    emit: Callable[[str, str, TaskState, dict | None], None]
    sku_index: int = 0
    total_skus: int = 1
    category_profile: DynamicCategoryProfile | None = None

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
            highlights = list(progress["highlights"])
            findings = [finding_from_dict(item) for item in progress["findings"]]
        else:
            self._emit_phase(candidate, phase=1)
            emit_live_step(
                f"采集官方规格中 · {candidate.sku}",
                sku=candidate.sku,
                phase=1,
                url=candidate.source_url or "",
            )
            official_specs, highlights = self.collector.collect_official_specs(
                candidate,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
            )
            official_specs, highlights = self._align_specs_to_profile(
                official_specs, highlights, candidate.category
            )
            spec_highlights = self._spec_preview_lines(official_specs, highlights)
            self.emit(
                "specs_collected",
                f"已采集官方规格 {len(official_specs)} 项 · {candidate.sku}",
                TaskState.RUNNING,
                {
                    "sku": candidate.sku,
                    "sku_index": self.sku_index,
                    "total_skus": self.total_skus,
                    "phase": 1,
                    "official_specs": [to_dict(spec) for spec in official_specs],
                    "spec_highlights": highlights,
                    "spec_count": len(official_specs),
                    "highlights": spec_highlights,
                },
            )
            emit_live_step(
                f"已采集官方规格 {len(official_specs)} 项 · {candidate.sku}",
                sku=candidate.sku,
                phase=1,
                highlights=spec_highlights,
                detail=f"共 {len(official_specs)} 项参数",
            )

            self._emit_phase(candidate, phase=2)
            emit_live_step(
                f"采集真实口碑中 · {candidate.sku}",
                sku=candidate.sku,
                phase=2,
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
            emit_live_step(
                f"口碑脱水中 · {candidate.sku}",
                sku=candidate.sku,
                phase=2,
                detail=f"语料 {len(corpus)} 条",
            )
            findings = self.router.extract_real_world_findings(candidate.sku, corpus)
            finding_previews = [
                f"{item.title}: {item.detail[:60]}".strip(": ")
                for item in findings[:4]
                if item.title or item.detail
            ]
            self.emit(
                "findings_extracted",
                f"已提取真实口碑 {len(findings)} 条（语料 {len(corpus)}）· {candidate.sku}",
                TaskState.RUNNING,
                {
                    "sku": candidate.sku,
                    "sku_index": self.sku_index,
                    "total_skus": self.total_skus,
                    "phase": 2,
                    "findings": [to_dict(finding) for finding in findings],
                    "corpus_size": len(corpus),
                    "finding_count": len(findings),
                    "highlights": finding_previews,
                },
            )
            if finding_previews:
                emit_live_step(
                    f"已提取口碑 {len(findings)} 条 · {candidate.sku}",
                    sku=candidate.sku,
                    phase=2,
                    highlights=finding_previews,
                )

        self._emit_phase(candidate, phase=3)
        emit_live_step(
            f"采集到手价中 · {candidate.sku}",
            sku=candidate.sku,
            phase=3,
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
        if prices:
            price_lines = [f"{p.platform}: ¥{p.final_price:g}" for p in prices[:4]]
            emit_live_step(
                f"已拿到价格 {len(prices)} 条 · {candidate.sku}",
                sku=candidate.sku,
                phase=3,
                highlights=price_lines,
            )

        self._emit_phase(candidate, phase=4)
        emit_live_step(
            f"冲突仲裁中 · {candidate.sku}",
            sku=candidate.sku,
            phase=4,
        )
        warnings = self.router.arbitrate_conflicts(
            findings, official_specs, category=candidate.category
        )
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

    @staticmethod
    def _spec_preview_lines(official_specs: list[OfficialSpec], highlights: list[str]) -> list[str]:
        lines: list[str] = []
        for spec in official_specs[:6]:
            unit = f" {spec.unit}" if spec.unit else ""
            lines.append(f"{spec.name}: {spec.value}{unit}".strip())
        for tip in highlights[:3]:
            if tip and tip not in lines:
                lines.append(tip)
        return lines[:8]

    def _align_specs_to_profile(
        self,
        official_specs: list[OfficialSpec],
        highlights: list[str],
        category: str,
    ) -> tuple[list[OfficialSpec], list[str]]:
        profile = self.category_profile
        slots = set(canonical_slots(category, profile=profile))
        is_generic = not profile or profile.source == "generic"
        aligned: list[OfficialSpec] = []
        seen: set[str] = set()
        extra_highlights = list(highlights)
        for spec in official_specs:
            name = map_spec_name_to_slot(spec.name, category, profile=profile)
            if name in seen:
                continue
            seen.add(name)
            if name in slots:
                aligned.append(
                    OfficialSpec(
                        name=name,
                        value=spec.value,
                        unit=spec.unit,
                        source_url=spec.source_url,
                    )
                )
            elif not is_generic and name not in slots:
                tip = f"{name}: {spec.value}".strip(": ")
                if tip and tip not in extra_highlights and len(extra_highlights) < 12:
                    extra_highlights.append(tip)
            else:
                aligned.append(
                    OfficialSpec(
                        name=name,
                        value=spec.value,
                        unit=spec.unit,
                        source_url=spec.source_url,
                    )
                )
        if not aligned and official_specs:
            for spec in official_specs:
                name = map_spec_name_to_slot(spec.name, category, profile=profile)
                aligned.append(
                    OfficialSpec(
                        name=name,
                        value=spec.value,
                        unit=spec.unit,
                        source_url=spec.source_url,
                    )
                )
        return aligned, extra_highlights[:12]

    def _emit_phase(self, candidate: ProductCandidate, *, phase: int) -> None:
        label = PHASE_LABELS.get(phase, f"阶段 {phase}")
        started = now_iso()
        self.emit(
            "phase_started",
            f"[{self.sku_index + 1}/{self.total_skus}] 步骤 {phase}/4 · {label} · {candidate.sku}",
            TaskState.RUNNING,
            {
                "sku": candidate.sku,
                "sku_index": self.sku_index,
                "total_skus": self.total_skus,
                "phase": phase,
                "phase_label": label,
                "category": candidate.category,
                "action": label,
                "started_at": started,
                "progress": round(
                    (self.sku_index * 4 + max(phase - 1, 0)) / max(self.total_skus * 4, 1),
                    3,
                ),
            },
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
            emit_live_step(
                f"Gemini OCR 识价中 · {candidate.sku}",
                sku=candidate.sku,
                phase=3,
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
            if hasattr(self.collector, "diagnostics"):
                self.collector.diagnostics.record(
                    "price",
                    f"price stage soft-skip auth for {candidate.sku}: {exc}",
                    level="warning",
                    sku=candidate.sku,
                )
            self.emit(
                "price_degraded",
                f"价格鉴权已跳过，继续用已有证据 · {candidate.sku}",
                TaskState.RUNNING,
                {
                    "sku": candidate.sku,
                    "sku_index": self.sku_index,
                    "total_skus": self.total_skus,
                    "phase": 3,
                    "error": str(exc),
                },
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
                f"价格阶段降级，仅保留规格/口碑 · {candidate.sku}",
                TaskState.RUNNING,
                {
                    "sku": candidate.sku,
                    "sku_index": self.sku_index,
                    "total_skus": self.total_skus,
                    "phase": 3,
                    "error": str(exc),
                },
            )
            return []
