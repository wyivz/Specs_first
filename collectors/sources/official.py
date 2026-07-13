from __future__ import annotations

from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    candidate_from_search_result,
    extract_specs_from_text,
    infer_specs_from_sku,
)
from collectors.http import HttpClient, SearchResult, clip, extract_title
from collectors.protocols import SpecExtractionRouter
from collectors.resilient_fetch import ResilientFetcher
from collectors.url_guards import is_noisy_ecommerce_url
from schemas import OfficialSpec, ProductCandidate


class OfficialSourceCollector:
    OFFICIAL_HINTS = [
        "official",
        "specifications",
        "manual",
        "white paper",
        "datasheet",
        "官网",
        "规格",
        "说明书",
        "白皮书",
    ]

    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
        *,
        router: SpecExtractionRouter | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)
        self.router = router

    def discover_candidates(self, query: str, category: str, max_results: int = 10) -> list[ProductCandidate]:
        search_query = f"{query} {category} official specifications"
        results = self.http.search(search_query, max_results=max_results * 2)
        candidates: list[ProductCandidate] = []
        for result in results:
            if self._looks_relevant(result):
                candidates.append(candidate_from_search_result(result, category))
        if not candidates:
            candidates = [
                ProductCandidate(
                    sku=query.strip() or "Unknown Product",
                    brand=query.split()[0] if query.split() else "Unknown",
                    category=category,
                    source_url=results[0].url if results else "https://example.invalid/no-source",
                    confidence=0.35,
                )
            ]
        return candidates[:max_results]

    def collect_specs(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
        extra_urls: list[str] | None = None,
    ) -> tuple[list[OfficialSpec], list[str]]:
        urls = [*(extra_urls or []), candidate.source_url]
        urls.extend(
            result.url
            for result in self.http.search(f"{candidate.sku} official specifications", max_results=5)
            if self._looks_relevant(result)
        )

        specs_by_name: dict[str, OfficialSpec] = {}
        highlights: list[str] = []
        page_texts: list[str] = []
        for url in dict.fromkeys(urls):
            if not url.startswith("http"):
                continue
            if is_noisy_ecommerce_url(url):
                self.diagnostics.record(
                    "official",
                    f"skip noisy ecommerce url during official fetch: {url}",
                    level="info",
                    sku=candidate.sku,
                )
                continue
            snapshot = self.resilient.fetch(
                url,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
                sku=candidate.sku,
            )
            if is_noisy_ecommerce_url(snapshot.url):
                self.diagnostics.record(
                    "official",
                    f"skip redirected noisy ecommerce page: {url} -> {snapshot.url}",
                    level="info",
                    sku=candidate.sku,
                )
                continue
            if not snapshot.ok:
                self.diagnostics.record(
                    "official",
                    f"weak page snapshot for {url}: {snapshot.error or snapshot.page.blockers}",
                    level="warning",
                    sku=candidate.sku,
                )
                if not snapshot.markup:
                    continue
            text = snapshot.text
            page_texts.append(text)
            for spec in extract_specs_from_text(text, snapshot.url, candidate.category):
                specs_by_name.setdefault(spec.name, spec)
            title = snapshot.page.title or extract_title(snapshot.markup)
            if title and len(highlights) < 3:
                highlights.append(clip(title, 80))

        combined_text = "\n\n".join(page_texts)
        if combined_text.strip() and self.router is not None:
            try:
                gemini_specs, gemini_highlights = self.router.extract_official_specs_from_text(
                    candidate.sku,
                    combined_text,
                    candidate.source_url,
                    category=candidate.category,
                )
                for spec in gemini_specs:
                    specs_by_name.setdefault(spec.name, spec)
                for item in gemini_highlights:
                    if item not in highlights and len(highlights) < 5:
                        highlights.append(item)
            except Exception:
                pass

        for spec in infer_specs_from_sku(candidate):
            specs_by_name.setdefault(spec.name, spec)
        return list(specs_by_name.values()), highlights

    def _looks_relevant(self, result: SearchResult) -> bool:
        if is_noisy_ecommerce_url(result.url):
            return False
        combined = f"{result.title} {result.snippet} {result.url}".lower()
        return any(hint in combined for hint in self.OFFICIAL_HINTS)
