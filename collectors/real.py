from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.base import Collector
from collectors.browser import PlaywrightCapture
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

    def __post_init__(self) -> None:
        browser = PlaywrightCapture()
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
        )
        merged: dict[str, OfficialSpec] = {spec.name: spec for spec in ecommerce_specs}
        for spec in official_specs:
            merged.setdefault(spec.name, spec)
        highlights = [*ecommerce_highlights, *official_highlights]
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
            return dedupe_evidence(evidence)

    def collect_prices(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        with get_collection_guard():
            prices = []
            prices.extend(
                self.ecommerce.collect(
                    candidate,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                )
            )
            prices.extend(
                self.injected.collect_prices(
                    self.source_urls,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                )
            )
            return sorted(prices, key=lambda item: item.final_price)[:5]

    def diagnostics_report(self) -> list[dict[str, Any]]:
        return self.diagnostics.to_dicts()
