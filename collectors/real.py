from __future__ import annotations

from dataclasses import dataclass, field

from collectors.base import Collector
from collectors.extractors import dedupe_evidence
from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import HttpClient
from collectors.platform_auth import PlatformAuthRequired
from collectors.rate_limit import get_collection_guard
from collectors.resilient_fetch import ResilientFetcher
from collectors.sources import (
    EcommerceSourceCollector,
    ForumSourceCollector,
    OfficialSourceCollector,
    UrlInjectionCollector,
    VideoSourceCollector,
)
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate


@dataclass
class RealCollector(Collector):
    source_urls: list[str] = field(default_factory=list)
    http: HttpClient = field(default_factory=HttpClient)
    diagnostics: CollectorDiagnostics = field(default_factory=CollectorDiagnostics)

    def __post_init__(self) -> None:
        browser = PlaywrightCapture()
        self.resilient = ResilientFetcher(self.http, browser, self.diagnostics)
        self.official = OfficialSourceCollector(self.http, self.diagnostics, self.resilient)
        self.video = VideoSourceCollector(self.http, self.diagnostics, self.resilient)
        self.forum = ForumSourceCollector(self.http, self.diagnostics, self.resilient)
        self.ecommerce = EcommerceSourceCollector(self.http, self.diagnostics, browser, self.resilient)
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
        return self.official.collect_specs(
            candidate,
            task_id=task_id,
            use_browser=use_browser,
            storage_state_path=storage_state_path,
        )

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

    def diagnostics_report(self) -> list[dict]:
        return self.diagnostics.to_dicts()
