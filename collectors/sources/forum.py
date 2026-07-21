from __future__ import annotations

from collectors.credentials import load_reddit_credentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    dedupe_evidence,
    evidence_from_page,
    evidence_from_search_result,
    evidence_mentions_sku,
    page_matches_sku,
)
from collectors.http import HttpClient, SearchResult
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, ProductCandidate
from schemas.category_profile import (
    DynamicCategoryProfile,
    forum_search_queries,
    rank_search_results_for_reviews,
)


class ForumSourceCollector:
    _PROVISIONAL_FETCH_LIMIT = 1

    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)
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
        include_reddit = load_reddit_credentials().configured
        max_results = 3 if not use_browser else 8
        modifiers = self._search_modifiers()
        base_by_platform = dict(
            forum_search_queries(candidate.sku, include_reddit=include_reddit, modifiers=None)
        )
        for platform, query in forum_search_queries(
            candidate.sku,
            include_reddit=include_reddit,
            modifiers=modifiers,
        ):
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
                from collectors.url_guards import is_noisy_forum_url

                if is_noisy_forum_url(result.url):
                    self.diagnostics.record(
                        platform,
                        f"skip noisy forum url: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
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
                    platform, result, confidence=0.57, sku=candidate.sku
                )
                if search_evidence:
                    evidence.append(search_evidence)
                # Reddit/Chiphell: HTTP(+Cookie) first; only escalate when caller opts in
                # or resilient_fetch decides the HTTP payload is weak/blocked.
                page = self.resilient.fetch(
                    result.url,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                if page.page.is_blocked:
                    self.diagnostics.record(
                        platform,
                        f"skip blocked forum page: {result.url} ({page.page.blockers})",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                if page.ok or page.markup:
                    page_title = getattr(page.page, "title", "") or ""
                    page_text = getattr(page.page, "text", "") or ""
                    if not page_matches_sku(
                        candidate.sku,
                        title=page_title,
                        text=page_text,
                        url=page.url,
                    ):
                        self.diagnostics.record(
                            platform,
                            f"skip page that does not match target sku: {result.url}",
                            level="info",
                            sku=candidate.sku,
                        )
                        continue
                    evidence.extend(
                        evidence_from_page(
                            platform,
                            page.url,
                            page.markup,
                            confidence=0.64,
                            sku=candidate.sku,
                        )
                    )
                else:
                    self.diagnostics.record(
                        platform,
                        f"failed to fetch {result.url}: {page.error or page.page.blockers}",
                        sku=candidate.sku,
                    )
        return dedupe_evidence(evidence)
