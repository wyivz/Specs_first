from __future__ import annotations

from collectors.credentials import load_reddit_credentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    dedupe_evidence,
    evidence_from_page,
    evidence_from_search_result,
    evidence_mentions_sku,
)
from collectors.http import HttpClient
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, ProductCandidate
from schemas.category_profile import (
    DynamicCategoryProfile,
    forum_search_queries,
    rank_search_results_for_reviews,
)


class ForumSourceCollector:
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
        for platform, query in forum_search_queries(
            candidate.sku,
            include_reddit=include_reddit,
            modifiers=self._search_modifiers(),
        ):
            ranked = rank_search_results_for_reviews(
                self.http.search(query, max_results=max_results),
                sku=candidate.sku,
            )
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
                if page.ok or page.markup:
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
