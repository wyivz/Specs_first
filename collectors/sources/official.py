from __future__ import annotations

from collections.abc import Callable
import re

from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    evidence_mentions_sku,
    extract_specs_from_text,
    infer_specs_from_sku,
    page_matches_sku,
    primary_model_code,
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

    def collect_discovery_hits(
        self,
        query: str,
        category: str,
        max_results: int = 10,
        *,
        quick: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[SearchResult]:
        """Search the open web for discovery evidence (no SKU parsing here)."""
        seed = (query or "").strip()
        english = _english_discovery_alias(seed)
        if quick:
            search_plans = [
                f"{seed} 型号".strip(),
                f"{seed} models comparison".strip(),
            ]
            if english and english.casefold() != seed.casefold():
                search_plans.append(f"{english} models")
                search_plans.append(f"{english} comparison buy")
        else:
            search_plans = [
                f"{seed} 型号 推荐".strip(),
                f"{seed} {category} models".strip(),
                f"{seed} review specifications".strip(),
                f"{seed} official".strip(),
            ]
            if english and english.casefold() != seed.casefold():
                search_plans.insert(1, f"{english} models comparison")
        # De-dupe while preserving order.
        search_plans = list(dict.fromkeys(p for p in search_plans if p))
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
            tokens = [
                token
                for token in re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
                if len(token) >= 2
            ]
            if tokens and sum(1 for token in tokens if token in combined) < max(1, min(2, len(tokens) // 2)):
                return False
        return True


# Bilingual brand + common category nouns for discovery search expansion.
# Brands are lookups (any vertical); nouns are generic shopping vocabulary —
# not a camera-only ontology. Prefer DynamicCategoryProfile modifiers when present.
_ZH_EN_DISCOVERY_TERMS: tuple[tuple[str, str], ...] = (
    ("罗技", "Logitech"),
    ("雷蛇", "Razer"),
    ("雷柏", "Rapoo"),
    ("赛睿", "SteelSeries"),
    ("漫步者", "Edifier"),
    ("樱桃", "Cherry"),
    ("苹果", "Apple"),
    ("三星", "Samsung"),
    ("小米", "Xiaomi"),
    ("华为", "Huawei"),
    ("索尼", "Sony"),
    ("微软", "Microsoft"),
    ("佳能", "Canon"),
    ("尼康", "Nikon"),
    ("大疆", "DJI"),
    ("蔡司", "Zeiss"),
    ("适马", "Sigma"),
    ("腾龙", "Tamron"),
    ("徕卡", "Leica"),
    ("富士", "Fujifilm"),
    ("松下", "Panasonic"),
    ("鼠标", "mouse"),
    ("键盘", "keyboard"),
    ("耳机", "headphones"),
    ("音箱", "speaker"),
    ("显示器", "monitor"),
    ("笔记本", "laptop"),
    ("手机", "phone"),
    ("相机", "camera"),
    ("镜头", "lens"),
    ("无人机", "drone"),
)


def _english_discovery_alias(query: str) -> str:
    """Map common Chinese shopping phrases to English search aliases for DDG/ddgs."""
    text = (query or "").strip()
    if not text or not re.search(r"[\u4e00-\u9fff]", text):
        return ""
    alias = text
    for zh, en in _ZH_EN_DISCOVERY_TERMS:
        if zh in alias:
            alias = alias.replace(zh, f" {en} ")
    alias = re.sub(r"\s+", " ", alias).strip()
    # Drop leftover CJK so the English plan stays searchable abroad.
    alias = re.sub(r"[\u4e00-\u9fff]+", " ", alias)
    alias = re.sub(r"\s+", " ", alias).strip()
    return alias
