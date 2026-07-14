from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


# Generic evaluation slots used when no JIT profile is available (no LLM / failure).
GENERIC_PARAMETER_SLOTS = tuple(f"parameter_{chr(ord('a') + index)}" for index in range(8))

# Cross-profile synonyms so extracted labels align with JIT slots.
BUILTIN_SPEC_SLOT_MAP: dict[str, str] = {
    "maximum_aperture": "max_aperture",
    "maximum_aperture_f": "max_aperture",
    "max_aperture_f": "max_aperture",
    "aperture": "max_aperture",
    "lens_mount": "mount",
    "mount_type": "mount",
    "minimum_focus_distance": "min_focus_distance",
    "min_focus": "min_focus_distance",
    "closest_focus": "min_focus_distance",
    "focal_length_mm": "focal_length",
    "filter_size": "filter_diameter",
    "lens_weight": "weight",
    "product_weight": "weight",
    "image_stabilisation": "image_stabilization",
    "stabilization": "image_stabilization",
}


def slugify_spec_name(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", label.strip().lower())
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", cleaned, flags=re.UNICODE)
    cleaned = cleaned.strip("_")
    return cleaned or "parameter"


@dataclass
class DynamicCategoryProfile:
    """JIT category schema: 5-8 hard comparison slots + aliases + search keywords.

    Built by ChatGPT Structured Outputs after Gemini vision survey (or from
    query text alone when no images are available). Persisted on the task
    checkpoint so every SKU in a compare run shares one column schema.
    """

    category_label: str = "通用商品"
    slots: list[str] = field(default_factory=lambda: list(GENERIC_PARAMETER_SLOTS))
    aliases: dict[str, str] = field(default_factory=dict)
    comparison_keywords: list[str] = field(default_factory=list)
    search_modifiers: list[str] = field(default_factory=list)
    source: str = "generic"  # openai_jit | generic

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DynamicCategoryProfile:
        if not data:
            return generic_category_profile()
        slots_raw = data.get("slots") or list(GENERIC_PARAMETER_SLOTS)
        slots = [_normalize_slot_key(str(s)) for s in slots_raw if str(s).strip()]
        slots = _clamp_slots(slots)
        aliases_raw = data.get("aliases") or {}
        aliases: dict[str, str] = {}
        if isinstance(aliases_raw, dict):
            for alias, slot in aliases_raw.items():
                key = str(alias).strip().lower()
                val = _normalize_slot_key(str(slot))
                if key and val:
                    aliases[key] = val
        elif isinstance(aliases_raw, list):
            for item in aliases_raw:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("alias", "")).strip().lower()
                val = _normalize_slot_key(str(item.get("slot", "")))
                if key and val:
                    aliases[key] = val
        label = str(data.get("category_label") or "").strip() or "通用商品"
        source = str(data.get("source") or "generic").strip() or "generic"
        comparison_keywords = [
            str(item).strip() for item in (data.get("comparison_keywords") or []) if str(item).strip()
        ]
        search_modifiers = [
            str(item).strip() for item in (data.get("search_modifiers") or []) if str(item).strip()
        ]
        return cls(
            category_label=label,
            slots=slots,
            aliases=aliases,
            comparison_keywords=comparison_keywords,
            search_modifiers=search_modifiers,
            source=source,
        )


def _normalize_slot_key(raw: str) -> str:
    return slugify_spec_name(raw.replace("-", "_"))


def _clamp_slots(slots: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for slot in slots:
        if not slot or slot in seen:
            continue
        seen.add(slot)
        cleaned.append(slot)
        if len(cleaned) >= 8:
            break
    while len(cleaned) < 5:
        filler = f"parameter_{chr(ord('a') + len(cleaned))}"
        if filler not in seen:
            cleaned.append(filler)
            seen.add(filler)
        else:
            cleaned.append(f"extra_slot_{len(cleaned)}")
            seen.add(cleaned[-1])
    return cleaned


def generic_category_profile(category_label: str = "通用商品") -> DynamicCategoryProfile:
    label = (category_label or "").strip()
    placeholder = label.lower() in {"", "product", "品类", "generic", "通用", "通用商品"}
    return DynamicCategoryProfile(
        category_label="通用商品" if placeholder else label,
        slots=list(GENERIC_PARAMETER_SLOTS),
        aliases={},
        comparison_keywords=[],
        search_modifiers=[],
        source="generic",
    )


def infer_category(query: str = "", category_hint: str = "") -> str:
    """Return a display label before JIT schema exists.

    No preset keyword matching: keep a non-placeholder user hint, else
    ``通用商品``. The real category label comes from ChatGPT JIT later.
    """
    del query  # reserved for callers; JIT uses query separately
    hint = (category_hint or "").strip()
    placeholder = hint.lower() in {"", "product", "品类", "generic", "通用", "通用商品"}
    if hint and not placeholder:
        return hint
    return "通用商品"


def resolve_category_key(category: str) -> str:
    """Stable opaque key for logging/events (slug of label, or ``generic``)."""
    lowered = (category or "").strip().lower()
    if not lowered or lowered in {"product", "品类", "generic", "通用", "通用商品"}:
        return "generic"
    return slugify_spec_name(category)


def category_template_key(query: str = "", category: str = "") -> str:
    """Backward-compatible alias for ``resolve_category_key``."""
    return resolve_category_key(infer_category(query, category) if category or query else category)


def canonical_slots(
    category: str = "",
    profile: DynamicCategoryProfile | None = None,
) -> tuple[str, ...]:
    """Hard-spec column names from a JIT profile, else generic ``parameter_a..h``."""
    if profile and profile.slots:
        return tuple(profile.slots)
    del category
    return GENERIC_PARAMETER_SLOTS


def normalize_spec_name(
    label: str,
    category: str = "",
    profile: DynamicCategoryProfile | None = None,
) -> str:
    """Normalize an extracted spec label onto a canonical column name."""
    del category
    lowered = label.strip().lower()
    if profile and profile.aliases:
        for alias, canonical in sorted(profile.aliases.items(), key=lambda item: -len(item[0])):
            if alias in lowered:
                return canonical
    slug = slugify_spec_name(label)
    if profile and slug in profile.slots:
        return slug
    mapped = BUILTIN_SPEC_SLOT_MAP.get(slug, slug)
    if profile and mapped in profile.slots:
        return mapped
    return mapped


def map_spec_name_to_slot(
    label: str,
    category: str = "",
    profile: DynamicCategoryProfile | None = None,
) -> str:
    """Map a raw extracted label onto the best profile slot when possible."""
    normalized = normalize_spec_name(label, category, profile=profile)
    if profile and normalized in profile.slots:
        return normalized
    if normalized in BUILTIN_SPEC_SLOT_MAP.values() and profile:
        for slot in profile.slots:
            if slot == normalized or BUILTIN_SPEC_SLOT_MAP.get(slot) == normalized:
                return slot
    return normalized


def _append_modifiers(base: str, modifiers: list[str] | None) -> str:
    if not modifiers:
        return base
    extra = " ".join(m for m in modifiers if m).strip()
    if not extra:
        return base
    return f"{base} {extra}"


def video_search_queries(
    sku: str,
    *,
    modifiers: list[str] | None = None,
) -> list[tuple[str, str]]:
    from collectors.extractors import sku_search_phrase

    phrase = sku_search_phrase(sku)
    return [
        (
            "Bilibili",
            _append_modifiers(f"{phrase} site:bilibili.com 评测 缺点 问题 翻车 体验", modifiers),
        ),
        (
            "YouTube",
            _append_modifiers(
                f"{phrase} site:youtube.com review defect issue problem quality",
                modifiers,
            ),
        ),
    ]


_REVIEW_RANK_TOKENS: tuple[str, ...] = (
    "评测",
    "缺点",
    "翻车",
    "劝退",
    "问题",
    "对比",
    "体验",
    "开箱",
    "review",
    "vs",
    "problem",
    "issue",
    "defect",
    "cons",
    "disappoint",
)


def rank_search_results_for_reviews(results: list, sku: str = "") -> list:
    """Prefer hits that mention the target SKU, then review/defect wording.

    Stable for equal scores: original relative order is preserved via enumerate.
    """
    from collectors.extractors import evidence_mentions_sku, primary_model_code

    model = primary_model_code(sku).lower() if sku else ""

    def score(index_and_result: tuple[int, object]) -> tuple[int, int, int]:
        index, result = index_and_result
        title = str(getattr(result, "title", "") or "")
        snippet = str(getattr(result, "snippet", "") or "")
        url = str(getattr(result, "url", "") or "")
        text = f"{title} {snippet}".lower()
        sku_score = 0
        if sku:
            if evidence_mentions_sku(sku, title, snippet, url):
                sku_score = 20
        review_hits = sum(1 for token in _REVIEW_RANK_TOKENS if token.lower() in text)
        return (-sku_score, -review_hits, index)

    return [item for _, item in sorted(enumerate(results), key=score)]


def forum_search_queries(
    sku: str,
    *,
    include_reddit: bool = False,
    modifiers: list[str] | None = None,
) -> list[tuple[str, str]]:
    from collectors.extractors import sku_search_phrase

    phrase = sku_search_phrase(sku)
    compact = re.sub(r"[^a-z0-9]", "", (sku or "").lower())
    brand_hint = ""
    exclude_hint = ""
    if re.match(r"^sel\d+", compact):
        brand_hint = "Sony FE"
        exclude_hint = "-Canon -RF -Nikon -Sigma"
    chiphell_query = f"{phrase} {brand_hint} site:chiphell.com 缺点 品控 翻车 问题 体验 {exclude_hint}".strip()
    queries: list[tuple[str, str]] = [
        ("Chiphell", _append_modifiers(chiphell_query, modifiers)),
    ]
    if include_reddit:
        reddit_query = (
            f"{phrase} {brand_hint} site:reddit.com/r/SonyAlpha defect issue quality problem review {exclude_hint}"
        ).strip()
        queries.append(
            (
                "Reddit",
                _append_modifiers(reddit_query, modifiers),
            )
        )
    return queries


def ecommerce_search_queries(
    sku: str,
    *,
    modifiers: list[str] | None = None,
) -> list[tuple[str, str]]:
    # Prefer product hosts — bare site:jd.com / site:taobao.com returns campus,
    # music, brand and price-index junk that used to trigger false captcha pauses.
    # Intentionally ignore review ``search_modifiers``: product listing queries
    # must stay SKU + host only (评测/色散 etc. starve DDG of item.jd.com hits).
    del modifiers
    from collectors.extractors import sku_search_phrase

    phrase = sku_search_phrase(sku)
    return [
        ("JD", f"{phrase} site:item.jd.com"),
        (
            "Taobao/Tmall",
            f"{phrase} (site:detail.tmall.com OR site:item.taobao.com)",
        ),
    ]


def real_world_issue_patterns() -> list[str]:
    """Category-agnostic defect / complaint hints for evidence extraction."""
    return [
        r"缺陷|故障|损坏|broken|defect|fail(?:ure|ed)?",
        r"品控|质量问题|quality control|sample variation|unit variation",
        r"卡顿|延迟|lag|slow|unresponsive|sticky|sticks?",
        r"噪音|异响|noise|rattle|buzz",
        r"过热|overheat|thermal|温度",
        r"续航|battery life|standby drain",
        r"虚标|夸大|misleading|overpromise",
        r"劝退|翻车|regret|disappoint|avoid",
        r"售后|warranty|support|repair",
        r"紫边|色散|fringing|chromatic|aberration|flare|鬼影",
    ]


def review_content_patterns() -> list[str]:
    """Hints that a text snippet is a substantive user review, not boilerplate."""
    return [
        r"缺点|问题|不足|issue|problem|defect|complaint|concern",
        r"评测|review|体验|experience|hands-on|长期|after\s+\d+\s+(?:days|weeks|months)",
        r"翻车|劝退|regret|disappoint|not recommend",
    ]


def default_category() -> str:
    return "Product"
