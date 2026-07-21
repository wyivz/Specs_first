from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse
import re

from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    evidence_mentions_sku,
    extract_specs_from_text,
    infer_specs_from_sku,
    page_matches_sku,
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
        "specification",
        "manual",
        "white paper",
        "datasheet",
        "product page",
        "support",
        "官网",
        "规格",
        "说明书",
        "白皮书",
        "参数",
    ]
    SPEC_PATH_HINTS = (
        "/spec",
        "/specs",
        "/specification",
        "/product/",
        "/products/",
        "/support/",
        "/manual",
        "datasheet",
    )
    SOFT_PATH_DEMOTIONS = (
        "/article/",
        "/blog/",
        "/forum/",
        "/thread-",
        "forum.php",
        "/review/",
        "/reviews/",
        "/compare/",
        "/comparison/",
        "/news/",
        "/recommend/",
    )

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

    def collect_discovery_hits(
        self,
        query: str,
        category: str,
        max_results: int = 10,
        *,
        quick: bool = False,
        on_progress: Callable[[str], None] | None = None,
        search_plans: list[str] | None = None,
    ) -> list[SearchResult]:
        """Search the open web for discovery evidence (no SKU parsing here).

        Prefer caller-supplied ``search_plans`` from structured LLM expansion.
        Template fallbacks stay language-agnostic and do not use brand glossaries.
        """
        seed = (query or "").strip()
        if search_plans:
            plans = [str(item).strip() for item in search_plans if str(item).strip()]
        elif quick:
            plans = [
                f"{seed} 型号".strip(),
                f"{seed} models comparison".strip(),
                f"{seed} review".strip(),
            ]
        else:
            plans = [
                f"{seed} 型号 推荐".strip(),
                f"{seed} {category} models".strip(),
                f"{seed} review specifications".strip(),
                f"{seed} official".strip(),
            ]
        search_plans = list(dict.fromkeys(p for p in plans if p))
        per_plan = max(max_results, 6) if quick else max(max_results * 2, 8)

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for plan_index, plan in enumerate(search_plans, start=1):
            if not plan:
                continue
            if on_progress:
                on_progress(f"搜索 {plan_index}/{len(search_plans)}：{plan[:48]}")
            try:
                plan_hits = self.http.search(plan, max_results=per_plan, quick=quick)
            except TypeError:
                plan_hits = self.http.search(plan, max_results=per_plan)
            for result in plan_hits:
                if not result.url or result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                results.append(result)
            if quick and len(results) >= max(8, max_results):
                break

        self.last_discovery_hits = list(results)
        if not results:
            self.diagnostics.record(
                "official",
                f"search empty for discovery query: {seed or category}",
                level="warning",
            )
            if on_progress:
                on_progress("搜索引擎未返回结果，请换更具体关键词或检查网络")
        return results

    def discover_candidates(
        self,
        query: str,
        category: str,
        max_results: int = 10,
        *,
        quick: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[ProductCandidate]:
        """Legacy entry: collect hits only. SKU shortlist is produced by AI upstream."""
        self.collect_discovery_hits(
            query,
            category,
            max_results,
            quick=quick,
            on_progress=on_progress,
        )
        return []

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
        brand = (candidate.brand or "").strip()
        search_queries = [
            f"{candidate.sku} official specifications",
            f"{candidate.sku} specs 规格 参数",
        ]
        if brand and brand.lower() not in {"unknown", "n/a", "na", "-"}:
            search_queries.insert(0, f"{brand} {candidate.sku} official product specifications")
            search_queries.append(f"{brand} {candidate.sku} 规格 参数 官网")

        search_hits: list[SearchResult] = []
        seen_hit_urls: set[str] = set()
        for query in search_queries:
            for result in self.http.search(query, max_results=5):
                if not result.url or result.url in seen_hit_urls:
                    continue
                seen_hit_urls.add(result.url)
                search_hits.append(result)

        hard_hits = [
            result
            for result in search_hits
            if self._looks_relevant(result, query=candidate.sku, soft=False)
        ]
        soft_hits = [
            result
            for result in search_hits
            if self._looks_relevant(result, query=candidate.sku, soft=True)
        ]
        ranked = self._rank_official_results(hard_hits, brand=brand, sku=candidate.sku)
        if len(ranked) < 4:
            soft_only = [item for item in soft_hits if item.url not in {r.url for r in ranked}]
            ranked.extend(
                self._rank_official_results(soft_only, brand=brand, sku=candidate.sku)[: max(0, 4 - len(ranked))]
            )
        urls.extend(result.url for result in ranked)

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
            if self._is_soft_noise_url(url) and page_texts:
                self.diagnostics.record(
                    "official",
                    f"skip soft noise url after stronger hits: {url}",
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
            if not page_matches_sku(
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
            if not self._page_looks_like_specs(text) and not self._host_matches_brand(snapshot.url, brand):
                self.diagnostics.record(
                    "official",
                    f"skip low-spec-density page: {snapshot.url}",
                    level="info",
                    sku=candidate.sku,
                )
                if title and len(highlights) < 2:
                    highlights.append(clip(title, 80))
                continue
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
        if soft and self._is_soft_noise_url(result.url) and not any(hint in combined for hint in self.OFFICIAL_HINTS):
            return False
        if query and not evidence_mentions_sku(query, result.title, result.snippet, result.url):
            tokens = [
                token
                for token in re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
                if len(token) >= 2
            ]
            if tokens and sum(1 for token in tokens if token in combined) < max(1, min(2, len(tokens) // 2)):
                return False
        return True

    def _rank_official_results(
        self,
        results: list[SearchResult],
        *,
        brand: str,
        sku: str,
    ) -> list[SearchResult]:
        def score(result: SearchResult) -> tuple[int, int]:
            host = urlparse(result.url).netloc.lower()
            path = (urlparse(result.url).path or "").lower()
            combined = f"{result.title} {result.snippet} {result.url}".lower()
            points = 0
            if self._host_matches_brand(result.url, brand):
                points += 8
            if any(hint in path or hint in combined for hint in self.SPEC_PATH_HINTS):
                points += 5
            if any(hint in combined for hint in self.OFFICIAL_HINTS):
                points += 3
            if evidence_mentions_sku(sku, result.title, result.snippet, result.url):
                points += 2
            if self._is_soft_noise_url(result.url):
                points -= 6
            if host in {"", "www.google.com", "www.bing.com"}:
                points -= 4
            return (points, -len(host))

        return sorted(results, key=score, reverse=True)

    def _host_matches_brand(self, url: str, brand: str) -> bool:
        brand_slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (brand or "").lower())
        if len(brand_slug) < 2 or brand_slug in {"unknown", "na"}:
            return False
        host = urlparse(url).netloc.lower().replace("www.", "")
        host_compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", host)
        return brand_slug in host_compact

    def _is_soft_noise_url(self, url: str) -> bool:
        lower = (url or "").lower()
        return any(hint in lower for hint in self.SOFT_PATH_DEMOTIONS)

    def _page_looks_like_specs(self, text: str) -> bool:
        """Structural density check — not vertical keyword lists."""
        sample = (text or "")[:12000]
        if len(sample) < 120:
            return False
        unit_hits = len(
            re.findall(
                r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m|g|kg|hz|khz|mhz|ghz|w|wh|mah|v|a|inch|in|fps|mp|小时|分钟|升|ml|l)\b",
                sample,
                re.I,
            )
        )
        label_hits = len(
            re.findall(
                r"(规格|参数|specification|datasheet|重量|weight|接口|尺寸|容量|功率|续航|电池|材质|model|型号)",
                sample,
                re.I,
            )
        )
        kv_hits = len(re.findall(r"[A-Za-z\u4e00-\u9fff][^:：\n]{1,24}[:：]\s*\S+", sample))
        return unit_hits >= 3 or (unit_hits >= 1 and label_hits >= 2) or kv_hits >= 6
