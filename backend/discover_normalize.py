from __future__ import annotations

from typing import Any

from collectors.extractors import infer_brand, is_concrete_product_sku, sku_identity_key
from collectors.http import SearchResult
from schemas import ProductCandidate

__all__ = [
    "concrete_candidate_count",
    "discover_skus_from_evidence",
    "merge_discovery_candidates",
]

_DISCOVER_SYSTEM = (
    "You extract concrete, buyable product model names for a shopping comparison tool. "
    "Never return roundup/listicle titles (e.g. '五款推荐', '怎么选'). "
    "Return JSON only."
)


def concrete_candidate_count(candidates: list[ProductCandidate]) -> int:
    return sum(1 for item in candidates if is_concrete_product_sku(item.sku))


def merge_discovery_candidates(
    primary: list[ProductCandidate],
    secondary: list[ProductCandidate],
    *,
    max_results: int = 10,
) -> list[ProductCandidate]:
    from collectors.extractors import sku_identity_key

    merged: list[ProductCandidate] = []
    seen: set[str] = set()

    for item in [*primary, *secondary]:
        if not is_concrete_product_sku(item.sku):
            continue
        key = sku_identity_key(item.sku)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= max_results:
            break
    return merged


def discover_skus_from_evidence(
    query: str,
    hits: list[SearchResult],
    *,
    category: str = "Product",
    max_results: int = 10,
) -> list[ProductCandidate]:
    """Ask Gemini/OpenAI to turn search hits into concrete product SKUs.

    Returns an empty list when no API key is configured or the call fails.
    """
    from backend.config import settings

    if not hits:
        return []
    if not (settings.has_gemini or settings.has_openai):
        return []

    evidence_lines = []
    for index, hit in enumerate(hits[:12], start=1):
        evidence_lines.append(
            f"{index}. title={hit.title!r} url={hit.url!r} snippet={hit.snippet[:160]!r}"
        )
    prompt = (
        f"User query: {query!r}\n"
        f"Category hint: {category!r}\n"
        "From the search hits below, list 5-10 distinct buyable product models "
        "relevant to the query. Prefer specific model names (e.g. 'Logitech G304', "
        "'Razer Viper V3 Pro'), not article headlines.\n"
        "Return JSON: {\"products\":[{\"sku\":\"...\",\"brand\":\"...\"}]}\n\n"
        + "\n".join(evidence_lines)
    )

    payload: dict[str, Any] = {}
    try:
        if settings.has_gemini:
            payload = _gemini_discover(prompt)
        elif settings.has_openai:
            payload = _openai_discover(prompt)
    except Exception:
        return []

    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return []

    # Prefer a product-looking URL from the evidence when available.
    fallback_url = next((hit.url for hit in hits if hit.url.startswith("http")), "")
    candidates: list[ProductCandidate] = []
    for row in products:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()[:120]
        brand = str(row.get("brand") or "").strip() or infer_brand(sku)
        if not sku or not is_concrete_product_sku(sku):
            continue
        candidates.append(
            ProductCandidate(
                sku=sku,
                brand=brand,
                category=category,
                source_url=fallback_url or "https://example.invalid/llm-discover",
                confidence=0.74,
            )
        )
        if len(candidates) >= max_results:
            break
    return candidates


def _gemini_discover(prompt: str) -> dict[str, Any]:
    from backend.gemini_client import get_gemini_client
    from backend.router_schemas import parse_json_payload

    text = get_gemini_client().generate_text(
        prompt,
        task="json_extract",
        system_instruction=_DISCOVER_SYSTEM,
    )
    return parse_json_payload(text or "", default={"products": []})


def _openai_discover(prompt: str) -> dict[str, Any]:
    from openai import OpenAI

    from backend.config import settings
    from backend.router_schemas import parse_json_payload

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": _DISCOVER_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    text = (response.choices[0].message.content or "") if response.choices else ""
    return parse_json_payload(text, default={"products": []})
