from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from collectors.http import SearchResult, clip, strip_tags
from collectors.settings import settings
from schemas import ProductCandidate

__all__ = [
    "discover_skus_from_evidence",
    "expand_discovery_search_plans",
    "merge_discovery_candidates",
    "usable_discovered_sku",
    "sku_identity_key",
]

_DISCOVER_SYSTEM = """You turn web evidence into a shortlist of concrete, buyable product models
for a shopping comparison tool.

Rules:
- Return only specific sellable models/SKUs a shopper can pick (brand + model name/code).
- Never return article headlines, listicles, how-to titles, category names, shop names,
  brand-only names, or vague phrases.
- Never copy a search-result title into sku. Titles are navigation, not products.
- Prefer models explicitly named in page body text; titles/snippets are secondary hints.
- Only include models supported by the evidence. Do not invent unrelated products.
- Works for ANY product category.
- Prefer distinct models; drop duplicates and variant spam.
- JSON only.
"""

_EXPAND_SYSTEM = """You plan web search queries to find buyable product models for comparison.

Rules:
- Category-agnostic: work for mice, cameras, appliances, software, etc.
- Infer language variants and marketplace-oriented phrasings from the user query itself.
- Do NOT rely on a fixed brand glossary — only use brands/terms present or clearly implied.
- Prefer 2-5 short, diverse queries that will hit review/shopping pages.
- JSON only.
"""

PageFetcher = Callable[[str], str]


def usable_discovered_sku(sku: str) -> bool:
    """Structural gate only — semantics come from the LLM, not keyword blocklists."""
    text = (sku or "").strip()
    if not text or text.casefold() in {"unknown", "unknown product", "product", "n/a"}:
        return False
    if len(text) < 2 or len(text) > 80:
        return False
    return True


def sku_identity_key(sku: str) -> str:
    """Casefold alnum key for dedupe (language-agnostic)."""
    return "".join(ch for ch in (sku or "").casefold() if ch.isalnum())


def merge_discovery_candidates(
    primary: list[ProductCandidate],
    secondary: list[ProductCandidate],
    *,
    max_results: int = 10,
) -> list[ProductCandidate]:
    merged: list[ProductCandidate] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        if not usable_discovered_sku(item.sku):
            continue
        key = sku_identity_key(item.sku)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= max_results:
            break
    return merged


def _resolve_llm_json(
    llm_json: Callable[[str, str], dict[str, Any]] | None,
) -> Callable[[str, str], dict[str, Any]] | None:
    if llm_json is not None:
        return llm_json
    if not (settings.has_gemini or settings.has_openai):
        return None
    try:
        from backend.discovery_llm import create_discover_llm_json

        return create_discover_llm_json()
    except Exception:
        return None


def expand_discovery_search_plans(
    query: str,
    category: str = "Product",
    *,
    quick: bool = False,
    llm_json: Callable[[str, str], dict[str, Any]] | None = None,
    on_progress: Callable[[str], None] | None = None,
    max_plans: int = 5,
) -> list[str]:
    """Build discovery search queries via structured LLM output (no brand glossaries)."""
    seed = (query or "").strip()
    if not seed:
        return []
    # Minimal template fallback when LLM is unavailable — still category-agnostic.
    fallback = (
        [f"{seed} 型号", f"{seed} models comparison", f"{seed} review"]
        if quick
        else [
            f"{seed} 型号 推荐",
            f"{seed} {category} models".strip(),
            f"{seed} review specifications",
            f"{seed} official",
        ]
    )
    call_llm = _resolve_llm_json(llm_json)
    if call_llm is None:
        return list(dict.fromkeys(fallback))[:max_plans]

    if on_progress:
        on_progress("正在用 AI 规划发现检索词…")
    prompt = (
        f"User query: {seed!r}\n"
        f"Category hint (may be generic): {category!r}\n"
        f"Mode: {'quick' if quick else 'thorough'}\n"
        f"Return up to {max_plans} search_queries for finding concrete buyable models.\n"
        'JSON shape: {"search_queries":["..."]}\n'
    )
    try:
        payload = call_llm(_EXPAND_SYSTEM, prompt)
    except Exception as exc:
        if on_progress:
            on_progress(f"检索词规划失败，改用通用模板：{exc}")
        return list(dict.fromkeys(fallback))[:max_plans]

    raw = payload.get("search_queries") if isinstance(payload, dict) else None
    planned: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if text and text not in planned:
                planned.append(text)
    if not planned:
        return list(dict.fromkeys(fallback))[:max_plans]
    # Always keep the raw seed as a first-class plan.
    merged = [seed, *planned, *fallback]
    return list(dict.fromkeys(q for q in merged if q))[:max_plans]


def discover_skus_from_evidence(
    query: str,
    hits: list[SearchResult],
    *,
    category: str = "Product",
    max_results: int = 10,
    on_progress: Callable[[str], None] | None = None,
    llm_json: Callable[[str, str], dict[str, Any]] | None = None,
    page_fetcher: PageFetcher | None = None,
    fetch_bodies: bool = True,
    max_pages: int = 6,
    body_chars: int = 3500,
) -> list[ProductCandidate]:
    """Use Gemini/OpenAI over search hits + fetched page bodies → buyable models."""
    if not hits:
        if on_progress:
            on_progress("没有可用的搜索结果，无法提炼型号")
        return []

    call_llm = _resolve_llm_json(llm_json)
    if call_llm is None:
        if on_progress:
            on_progress("未配置 Gemini/OpenAI Key，无法提炼型号")
        return []

    bodies: dict[int, str] = {}
    if fetch_bodies and page_fetcher is not None:
        if on_progress:
            on_progress(f"正在抓取前 {min(max_pages, len(hits))} 个页面正文供 AI 阅读…")
        bodies = _fetch_page_bodies(
            hits,
            page_fetcher,
            max_pages=max_pages,
            body_chars=body_chars,
        )
        if on_progress:
            on_progress(f"已读取 {len(bodies)}/{min(max_pages, len(hits))} 页正文，正在用 AI 提炼型号…")
    elif on_progress:
        on_progress("正在用 AI 从搜索结果提炼可购型号…")

    evidence_lines = []
    for index, hit in enumerate(hits[:16], start=1):
        body = bodies.get(index, "")
        body_bit = f" body={body!r}" if body else " body=(not fetched)"
        evidence_lines.append(
            f"{index}. title={hit.title!r} url={hit.url!r} "
            f"snippet={(hit.snippet or '')[:200]!r}{body_bit}"
        )
    prompt = (
        f"User query: {query!r}\n"
        f"Category hint (may be generic): {category!r}\n"
        f"Return up to {max_results} distinct buyable product models relevant to the query.\n"
        "Read page body text when present; do not treat titles as products.\n"
        "For category queries (e.g. '无线鼠标' / '蓝牙耳机'), list current mainstream buyable models "
        "that the evidence supports — not the category phrase itself.\n"
        "Each item: sku (model name as sold), brand, evidence_index (1-based hit that supports it).\n"
        'JSON shape: {"products":[{"sku":"...","brand":"...","evidence_index":1}]}\n\n'
        "Evidence:\n"
        + "\n".join(evidence_lines)
    )

    try:
        payload = call_llm(_DISCOVER_SYSTEM, prompt)
    except Exception as exc:
        if on_progress:
            on_progress(f"AI 提炼失败：{exc}")
        return []

    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return []

    title_keys = {sku_identity_key(hit.title) for hit in hits if hit.title}
    candidates: list[ProductCandidate] = []
    seen: set[str] = set()
    for row in products:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()[:120]
        brand = str(row.get("brand") or "").strip() or _brand_guess(sku)
        if not usable_discovered_sku(sku):
            continue
        if _is_raw_search_title(sku, hits, title_keys):
            continue
        key = sku_identity_key(sku)
        if not key or key in seen:
            continue
        seen.add(key)

        source_url = "https://example.invalid/llm-discover"
        evidence_index = row.get("evidence_index")
        if isinstance(evidence_index, int) and 1 <= evidence_index <= len(hits):
            source_url = hits[evidence_index - 1].url or source_url
        elif hits:
            source_url = next((h.url for h in hits if (h.url or "").startswith("http")), source_url)

        candidates.append(
            ProductCandidate(
                sku=sku,
                brand=brand or "Unknown",
                category=category,
                source_url=source_url,
                confidence=0.82,
            )
        )
        if len(candidates) >= max_results:
            break
    return candidates


def _is_raw_search_title(sku: str, hits: list[SearchResult], title_keys: set[str]) -> bool:
    """Drop LLM outputs that are just copied search titles (not models named inside them)."""
    key = sku_identity_key(sku)
    if key and key in title_keys:
        return True
    sku_cf = (sku or "").casefold().strip()
    if len(sku_cf) < 12:
        return False
    for hit in hits:
        title = (hit.title or "").casefold().strip()
        if not title:
            continue
        if sku_cf == title:
            return True
        if len(title) >= 16 and title in sku_cf:
            return True
    return False


def _fetch_page_bodies(
    hits: list[SearchResult],
    page_fetcher: PageFetcher,
    *,
    max_pages: int,
    body_chars: int,
) -> dict[int, str]:
    targets = [(index, hit.url) for index, hit in enumerate(hits[:max_pages], start=1) if hit.url]
    if not targets:
        return {}

    bodies: dict[int, str] = {}

    def _one(item: tuple[int, str]) -> tuple[int, str]:
        index, url = item
        try:
            raw = page_fetcher(url) or ""
        except Exception:
            return index, ""
        text = clip(strip_tags(raw), body_chars)
        return index, text

    workers = min(4, len(targets))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, item) for item in targets]
        for future in as_completed(futures):
            index, text = future.result()
            if text.strip():
                bodies[index] = text
    return bodies


def _brand_guess(sku: str) -> str:
    parts = (sku or "").strip().split()
    return parts[0] if parts else "Unknown"
