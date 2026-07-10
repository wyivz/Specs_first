from __future__ import annotations

from collectors.credentials import load_reddit_credentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import dedupe_evidence, evidence_from_page, evidence_from_search_result
from collectors.http import HttpClient
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, ProductCandidate
from schemas.category_profile import forum_search_queries


def _reddit_browser_enabled(url: str, use_browser: bool) -> bool:
    return use_browser or "reddit.com" in url.lower()


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
        for platform, query in forum_search_queries(candidate.sku, include_reddit=include_reddit):
            for result in self.http.search(query, max_results=8):
                search_evidence = evidence_from_search_result(platform, result, confidence=0.57)
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.resilient.fetch(
                    result.url,
                    task_id=task_id,
                    use_browser=_reddit_browser_enabled(result.url, use_browser)
                    or "chiphell.com" in result.url.lower(),
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                if page.ok or page.markup:
                    evidence.extend(evidence_from_page(platform, page.url, page.markup, confidence=0.64))
                else:
                    self.diagnostics.record(
                        platform,
                        f"failed to fetch {result.url}: {page.error or page.page.blockers}",
                        sku=candidate.sku,
                    )
        return dedupe_evidence(evidence)
