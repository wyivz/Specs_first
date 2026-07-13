from __future__ import annotations

from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.adapters.youtube import YouTubeAdapter
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import dedupe_evidence, evidence_from_page, evidence_from_search_result, evidence_mentions_sku
from collectors.http import HttpClient
from collectors.platform_auth import PlatformAuthRequired
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, ProductCandidate
from schemas.category_profile import rank_search_results_for_reviews, video_search_queries


class VideoSourceCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
        *,
        registry: AdapterRegistry | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)
        self.registry = registry or create_default_registry(http=http, diagnostics=self.diagnostics)
        self.bilibili = self.registry.require(BilibiliAdapter)
        self.youtube = self.registry.require(YouTubeAdapter)

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        self.bilibili.reset_api_budget()
        max_results = 3 if not use_browser else 6
        for platform, query in video_search_queries(candidate.sku):
            ranked = rank_search_results_for_reviews(self.http.search(query, max_results=max_results))
            for result in ranked:
                if not evidence_mentions_sku(candidate.sku, result.title, result.snippet, result.url):
                    self.diagnostics.record(
                        platform,
                        f"skip unrelated search hit: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                search_evidence = evidence_from_search_result(
                    platform, result, confidence=0.52, sku=candidate.sku
                )
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.resilient.fetch(
                    result.url,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                if page.ok or page.markup:
                    try:
                        adapter = self.registry.for_url(page.url)
                        if adapter is not None and hasattr(adapter, "extract_evidence"):
                            try:
                                evidence.extend(
                                    adapter.extract_evidence(  # type: ignore[call-arg]
                                        page.url,
                                        page.markup,
                                        confidence=0.62,
                                        use_browser=use_browser,
                                    )
                                )
                            except TypeError:
                                evidence.extend(
                                    adapter.extract_evidence(page.url, page.markup, confidence=0.62)
                                )
                        else:
                            evidence.extend(
                                evidence_from_page(
                                    platform,
                                    page.url,
                                    page.markup,
                                    confidence=0.58,
                                    sku=candidate.sku,
                                )
                            )
                    except PlatformAuthRequired as exc:
                        exc.url = exc.url or page.url
                        raise
                else:
                    self.diagnostics.record(
                        platform,
                        f"failed to fetch {result.url}: {page.error or page.page.blockers}",
                        sku=candidate.sku,
                    )
        return dedupe_evidence(evidence)
