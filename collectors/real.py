from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.base import Collector
from collectors.browser import PlaywrightCapture
from collectors.collection_trace import create_collection_trace
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import HttpClient
from collectors.protocols import SpecExtractionRouter
from collectors.rate_limit import get_collection_guard
from collectors.resilient_fetch import ResilientFetcher
from collectors.sources import (
    EcommerceSourceCollector,
    ForumSourceCollector,
    OfficialSourceCollector,
    UrlInjectionCollector,
    VideoSourceCollector,
)
from collectors.extractors import dedupe_evidence
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate


@dataclass
class RealCollector(Collector):
    source_urls: list[str] = field(default_factory=list)
    http: HttpClient = field(default_factory=HttpClient)
    diagnostics: CollectorDiagnostics = field(default_factory=CollectorDiagnostics)
    router: SpecExtractionRouter | None = None
    registry: AdapterRegistry | None = None
    browser: Any | None = None

    def __post_init__(self) -> None:
        browser = self.browser if self.browser is not None else PlaywrightCapture()
        self.browser = browser
        self.resilient = ResilientFetcher(self.http, browser, self.diagnostics)
        registry = self.registry or create_default_registry(http=self.http, diagnostics=self.diagnostics)
        self.official = OfficialSourceCollector(
            self.http,
            self.diagnostics,
            self.resilient,
            router=self.router,
        )
        self.video = VideoSourceCollector(
            self.http,
            self.diagnostics,
            self.resilient,
            registry=registry,
        )
        self.forum = ForumSourceCollector(self.http, self.diagnostics, self.resilient)
        self.ecommerce = EcommerceSourceCollector(
            self.http,
            self.diagnostics,
            browser,
            self.resilient,
            registry=registry,
            router=self.router,
        )
        self.injected = UrlInjectionCollector(self.http, self.diagnostics, self.resilient)

    def _trace_context(self, task_id: str):
        trace = create_collection_trace(self.diagnostics, task_id=task_id)
        previous = self.resilient.trace
        self.resilient.trace = trace
        return trace, previous

    def _restore_trace(self, previous) -> None:
        self.resilient.trace = previous

    def discover_candidates(self, query: str, category: str) -> list[ProductCandidate]:
        return self.official.discover_candidates(query, category)

    def collect_official_specs(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        ecommerce_specs, ecommerce_highlights = self.ecommerce.collect_official_specs(
            candidate,
            task_id=task_id,
            use_browser=use_browser,
            storage_state_path=storage_state_path,
        )
        official_specs, official_highlights = self.official.collect_specs(
            candidate,
            task_id=task_id,
            use_browser=use_browser,
            storage_state_path=storage_state_path,
            extra_urls=self.source_urls,
        )
        merged: dict[str, OfficialSpec] = {spec.name: spec for spec in official_specs}
        for spec in ecommerce_specs:
            merged.setdefault(spec.name, spec)
        # Prefer official/manufacturer highlights over empty ecommerce metadata.
        highlights = [*official_highlights, *ecommerce_highlights]
        return list(merged.values()), highlights[:5]

    def collect_real_world_corpus(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[EvidenceItem]:
        with get_collection_guard():
            trace, previous = self._trace_context(task_id)
            try:
                if trace:
                    trace.log("collect", f"real_world_corpus sku={candidate.sku}", sku=candidate.sku)
                evidence = []
                evidence.extend(
                    self.video.collect(
                        candidate,
                        task_id=task_id,
                        use_browser=use_browser,
                        storage_state_path=storage_state_path,
                    )
                )
                evidence.extend(
                    self.forum.collect(
                        candidate,
                        task_id=task_id,
                        use_browser=use_browser,
                        storage_state_path=storage_state_path,
                    )
                )
                evidence.extend(
                    self.injected.collect_evidence(
                        self.source_urls,
                        task_id=task_id,
                        use_browser=use_browser,
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                )
                if trace:
                    trace.log("collect", f"real_world_corpus done evidence={len(evidence)}", sku=candidate.sku)
                return dedupe_evidence(evidence)
            finally:
                self._restore_trace(previous)

    def collect_prices(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        with get_collection_guard():
            trace, previous = self._trace_context(task_id)
            try:
                if trace:
                    trace.log("collect", f"prices sku={candidate.sku}", sku=candidate.sku)
                prices = []
                prices.extend(
                    self.ecommerce.collect(
                        candidate,
                        task_id=task_id,
                        use_browser=use_browser,
                        storage_state_path=storage_state_path,
                        trace=trace,
                    )
                )
                prices.extend(
                    self.injected.collect_prices(
                        self.source_urls,
                        task_id=task_id,
                        use_browser=use_browser,
                        storage_state_path=storage_state_path,
                        trace=trace,
                        sku=candidate.sku,
                    )
                )
                if trace:
                    trace.log("collect", f"prices done count={len(prices)}", sku=candidate.sku)
                return sorted(prices, key=lambda item: item.final_price)[:5]
            finally:
                self._restore_trace(previous)

    def diagnostics_report(self) -> list[dict[str, Any]]:
        return self.diagnostics.to_dicts()
