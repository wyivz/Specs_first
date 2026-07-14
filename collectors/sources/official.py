from __future__ import annotations

import re

from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    candidate_from_search_result,
    evidence_mentions_sku,
    extract_product_skus_from_hit,
    extract_specs_from_text,
    infer_brand,
    infer_specs_from_sku,
    is_category_or_list_url,
    is_concrete_product_sku,
    is_listicle_title,
    is_product_detail_url,
    page_matches_sku,
    primary_model_code,
    sku_identity_key,
)
from collectors.http import HttpClient, SearchResult, clip, extract_title
from collectors.protocols import SpecExtractionRouter
from collectors.resilient_fetch import ResilientFetcher
from collectors.url_guards import is_noisy_ecommerce_url
from schemas import OfficialSpec, ProductCandidate
from schemas.category_profile import DynamicCategoryProfile


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
        self.category_profile: DynamicCategoryProfile | None = None
        self.last_discovery_hits: list[SearchResult] = []

    def discover_candidates(self, query: str, category: str, max_results: int = 10) -> list[ProductCandidate]:
        seed = (query or "").strip()
        search_plans = [
            f"{seed} 型号 推荐 对比".strip(),
            f"{seed} {category} specs 参数".strip(),
            f"{seed} review specifications".strip(),
            f"{seed} {category} official".strip(),
        ]
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        candidates: list[ProductCandidate] = []
        for plan in search_plans:
            if not plan:
                continue
            for result in self.http.search(plan, max_results=max_results * 2):
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                results.append(result)
            self.last_discovery_hits = list(results)
            # Stop early once we have enough concrete product models.
            candidates = self._candidates_from_search_results(
                results,
                seed=seed,
                category=category,
                max_results=max_results,
                allow_weak_fallback=False,
            )
            if len(candidates) >= max_results:
                return candidates[:max_results]

        self.last_discovery_hits = list(results)
        if not results:
            self.diagnostics.record(
                "official",
                f"search empty for discovery query: {seed or category}",
                level="warning",
            )
        candidates = self._candidates_from_search_results(
            results,
            seed=seed,
            category=category,
            max_results=max_results,
            allow_weak_fallback=True,
        )
        if not candidates:
            candidates = [
                ProductCandidate(
                    sku=seed or "Unknown Product",
                    brand=infer_brand(seed) if seed else "Unknown",
                    category=category,
                    source_url="https://example.invalid/no-source",
                    confidence=0.35,
                )
            ]
        return candidates[:max_results]

    def _candidates_from_search_results(
        self,
        results: list[SearchResult],
        *,
        seed: str,
        category: str,
        max_results: int,
        allow_weak_fallback: bool,
    ) -> list[ProductCandidate]:
        ranked = _rank_discovery_results(results, query=seed)
        candidates: list[ProductCandidate] = []
        seen_keys: set[str] = set()

        def _add(candidate: ProductCandidate) -> bool:
            if not is_concrete_product_sku(candidate.sku):
                return False
            key = sku_identity_key(candidate.sku)
            if not key or key in seen_keys:
                return False
            seen_keys.add(key)
            candidates.append(candidate)
            return True

        seed_is_specific = bool(primary_model_code(seed)) or (
            is_concrete_product_sku(seed) and len(seed) <= 48
        )

        # Prefer explicit models extracted from any hit (including listicles).
        for result in ranked:
            if _discovery_conflicts_with_query(seed, result.title, result.snippet):
                continue
            for sku, brand in extract_product_skus_from_hit(result.title, result.snippet):
                confidence = 0.8 if is_product_detail_url(result.url) else 0.72
                _add(
                    ProductCandidate(
                        sku=sku,
                        brand=brand,
                        category=category,
                        source_url=result.url,
                        confidence=confidence,
                    )
                )
                if len(candidates) >= max_results:
                    return candidates

        # Product-detail pages with a concrete cleaned title.
        for result in ranked:
            if is_category_or_list_url(result.url) or is_listicle_title(result.title):
                continue
            if _discovery_conflicts_with_query(seed, result.title, result.snippet):
                continue
            if not _discovery_matches_query(seed, result.title, result.snippet, result.url):
                continue
            candidate = candidate_from_search_result(result, category)
            if seed_is_specific and primary_model_code(seed):
                # Keep the user's model when the query already names one.
                if evidence_mentions_sku(seed, result.title, result.snippet, result.url):
                    candidate = ProductCandidate(
                        sku=seed[:120],
                        brand=infer_brand(seed),
                        category=category,
                        source_url=result.url,
                        confidence=max(candidate.confidence, 0.82),
                    )
            _add(candidate)
            if len(candidates) >= max_results:
                return candidates

        if not candidates and allow_weak_fallback and seed_is_specific:
            _add(
                ProductCandidate(
                    sku=seed[:120],
                    brand=infer_brand(seed),
                    category=category,
                    source_url="https://example.invalid/query-seed",
                    confidence=0.4,
                )
            )
        return candidates

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
        search_hits = self.http.search(f"{candidate.sku} official specifications", max_results=5)
        if not search_hits:
            search_hits = self.http.search(f"{candidate.sku} specs 规格 参数", max_results=5)
        urls.extend(
            result.url
            for result in search_hits
            if self._looks_relevant(result, query=candidate.sku)
            or self._looks_relevant(result, query=candidate.sku, soft=True)
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
            # One retry for transient timeouts on manufacturer pages.
            if (not snapshot.ok) and "timed out" in (snapshot.error or "").lower():
                self.diagnostics.record(
                    "official",
                    f"retry after timeout for {url}",
                    level="info",
                    sku=candidate.sku,
                )
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
            title = snapshot.page.title or extract_title(snapshot.markup)
            if primary_model_code(candidate.sku) and not page_matches_sku(
                candidate.sku, title=title, text=snapshot.text, url=snapshot.url
            ):
                self.diagnostics.record(
                    "official",
                    f"skip page that does not match target sku: {snapshot.url}",
                    level="info",
                    sku=candidate.sku,
                )
                continue
            text = snapshot.text
            page_texts.append(text)
            for spec in extract_specs_from_text(
                text, snapshot.url, candidate.category, profile=self.category_profile
            ):
                specs_by_name.setdefault(spec.name, spec)
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

    def _looks_relevant(self, result: SearchResult, *, query: str = "", soft: bool = False) -> bool:
        if is_noisy_ecommerce_url(result.url):
            return False
        combined = f"{result.title} {result.snippet} {result.url}".lower()
        if not soft and not any(hint in combined for hint in self.OFFICIAL_HINTS):
            return False
        if query and not evidence_mentions_sku(query, result.title, result.snippet, result.url):
            if not _discovery_matches_query(query, result.title, result.snippet, result.url):
                return False
        return True


_DISCOVERY_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "product",
    "official",
    "specifications",
    "specs",
    "review",
    "品类",
    "通用",
    "对比",
    "评测",
    "参数",
}
_DOMAIN_MOUSE_HINTS = ("鼠标", "mouse", "dpi", "logitech", "罗技", "rapoo", "雷柏", "razer", "雷蛇")
_DOMAIN_LENS_HINTS = ("50mm", "35mm", "85mm", "镜头", " lens", "f/1.", "f/2", "gm ", "fe 50", "sel")
_DOMAIN_KEYBOARD_HINTS = ("键盘", "keyboard", "机械", "键帽", "轴体", "75%", "tkl")


def _discovery_tokens(query: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]+", (query or "").lower())
        if len(token) >= 2 and token not in _DISCOVERY_STOP_WORDS
    ]


def _discovery_matches_query(query: str, title: str, snippet: str = "", url: str = "") -> bool:
    if not (query or "").strip():
        return True
    if evidence_mentions_sku(query, title, snippet, url):
        return True
    blob = f"{title} {snippet} {url}".lower()
    tokens = _discovery_tokens(query)
    if not tokens:
        return True
    hits = sum(1 for token in tokens if token in blob)
    return hits >= max(1, min(2, len(tokens) // 2))


def _discovery_conflicts_with_query(query: str, title: str, snippet: str = "") -> bool:
    q = (query or "").lower()
    blob = f"{title} {snippet}".lower()
    if not q.strip():
        return False

    def _query_has(*hints: str) -> bool:
        return any(hint in q for hint in hints)

    def _blob_has(*hints: str) -> bool:
        return any(hint in blob for hint in hints)

    if _query_has(*_DOMAIN_MOUSE_HINTS) and _blob_has(*_DOMAIN_LENS_HINTS) and not _blob_has(*_DOMAIN_MOUSE_HINTS):
        return True
    if _query_has(*_DOMAIN_KEYBOARD_HINTS) and _blob_has(*_DOMAIN_LENS_HINTS) and not _blob_has(*_DOMAIN_KEYBOARD_HINTS):
        return True
    if _query_has(*_DOMAIN_LENS_HINTS) and _blob_has(*_DOMAIN_MOUSE_HINTS) and not _blob_has(*_DOMAIN_LENS_HINTS):
        return True
    return False


def _rank_discovery_results(results: list[SearchResult], *, query: str) -> list[SearchResult]:
    tokens = _discovery_tokens(query)

    def score(index_and_result: tuple[int, SearchResult]) -> tuple[int, int]:
        index, result = index_and_result
        blob = f"{result.title} {result.snippet}".lower()
        token_hits = sum(1 for token in tokens if token in blob)
        sku_hit = 20 if evidence_mentions_sku(query, result.title, result.snippet, result.url) else 0
        conflict = 1 if _discovery_conflicts_with_query(query, result.title, result.snippet) else 0
        return (-conflict, -(sku_hit + token_hits * 3), index)

    return [item for _, item in sorted(enumerate(results), key=score)]
