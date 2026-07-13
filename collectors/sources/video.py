from __future__ import annotations

from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.adapters.youtube import YouTubeAdapter
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import dedupe_evidence, evidence_from_page, evidence_from_search_result, evidence_mentions_sku
from collectors.http import HttpClient, SearchResult
from collectors.platform_auth import PlatformAuthRequired
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, ProductCandidate
from schemas.category_profile import DynamicCategoryProfile, rank_search_results_for_reviews, video_search_queries


class VideoSourceCollector:
    # Fetch top-N ranked hits even when the DDG snippet is too thin for SKU matching;
    # adapters re-check page titles after fetch.
    _PROVISIONAL_FETCH_LIMIT = 2

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
        self.category_profile: DynamicCategoryProfile | None = None

    def _search_modifiers(self) -> list[str] | None:
        if self.category_profile and self.category_profile.search_modifiers:
            return list(self.category_profile.search_modifiers)
        return None

    def _search_platform(
        self,
        platform: str,
        query: str,
        *,
        base_query: str,
        max_results: int,
        sku: str,
    ) -> list[SearchResult]:
        results = self.http.search(query, max_results=max_results)
        if results:
            return results
        if base_query and base_query != query:
            self.diagnostics.record(
                platform,
                f"search empty with modifiers; retrying without: {base_query}",
                level="info",
                sku=sku,
            )
            results = self.http.search(base_query, max_results=max_results)
        if not results:
            self.diagnostics.record(
                platform,
                f"search empty: {query}",
                level="warning",
                sku=sku,
            )
        return results

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
        modifiers = self._search_modifiers()
        base_by_platform = dict(video_search_queries(candidate.sku, modifiers=None))
        for platform, query in video_search_queries(candidate.sku, modifiers=modifiers):
            ranked = rank_search_results_for_reviews(
                self._search_platform(
                    platform,
                    query,
                    base_query=base_by_platform.get(platform, query),
                    max_results=max_results,
                    sku=candidate.sku,
                ),
                sku=candidate.sku,
            )
            for index, result in enumerate(ranked):
                sku_ok = evidence_mentions_sku(candidate.sku, result.title, result.snippet, result.url)
                if not sku_ok and index >= self._PROVISIONAL_FETCH_LIMIT:
                    self.diagnostics.record(
                        platform,
                        f"skip unrelated search hit: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                if not sku_ok:
                    self.diagnostics.record(
                        platform,
                        f"provisional fetch of thin-snippet hit: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
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
                                        sku=candidate.sku,
                                    )
                                )
                            except TypeError:
                                try:
                                    evidence.extend(
                                        adapter.extract_evidence(  # type: ignore[call-arg]
                                            page.url,
                                            page.markup,
                                            confidence=0.62,
                                            sku=candidate.sku,
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
                        # Soft-skip like ecommerce: one platform auth must not pause the whole task.
                        self.diagnostics.record(
                            platform,
                            f"soft-skip platform auth for {page.url}: {exc}",
                            level="warning",
                            sku=candidate.sku,
                        )
                        continue
                else:
                    self.diagnostics.record(
                        platform,
                        f"failed to fetch {result.url}: {page.error or page.page.blockers}",
                        sku=candidate.sku,
                    )
        return dedupe_evidence(evidence)
