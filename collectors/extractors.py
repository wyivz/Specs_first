from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

from collectors.page_sanitize import sanitize_html
from collectors.http import SearchResult, clip, normalize_whitespace, strip_tags
from schemas import EvidenceItem, OfficialSpec, ProductCandidate
from schemas.category_profile import (
    canonical_slots,
    normalize_spec_name,
    real_world_issue_patterns,
    review_content_patterns,
)


KEY_VALUE_SPEC_PATTERN = re.compile(
    r"(?:^|[\n;|])\s*([A-Za-z0-9\u4e00-\u9fff][^:：\n|]{1,48}?)\s*[:：]\s*([^\n;|]{1,120})",
    re.M,
)
MEASUREMENT_PATTERN = re.compile(
    r"\b([0-9]+(?:\.[0-9]+)?\s*(?:mm|cm|m|g|kg|gb|tb|hz|w|mah|inch|英寸|克|千克|毫米|厘米|米|%))\b",
    re.I,
)
TABLE_ROW_PATTERN = re.compile(r"<tr[^>]*>\s*(.*?)\s*</tr>", re.I | re.S)
TABLE_CELL_PATTERN = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
DETAIL_IMAGE_PATTERN = re.compile(
    r"""(?:src|data-src|data-lazyload|original|file-url)\s*=\s*["']([^"']+\.(?:jpg|jpeg|png|webp)(?:\?[^"']*)?)["']""",
    re.I,
)
DESC_API_PATTERN = re.compile(r"""["']((?:https?:)?//[^"']*(?:getdesc|desc|description)[^"']*)["']""", re.I)
SKIP_SPEC_LABELS = re.compile(
    r"(copyright|cookie|login|javascript|function|window|price|价格|购买|cart|subscribe)",
    re.I,
)

NEGATIVE_PATTERNS = real_world_issue_patterns()
REVIEW_PATTERNS = review_content_patterns()

PRICE_PATTERN = re.compile(
    r"(?:¥|￥|RMB|CNY|\$)?\s*([1-9][0-9]{2,6}(?:\.[0-9]{1,2})?)\s*(?:元|rmb|cny|usd)?",
    re.I,
)
FINAL_PRICE_PATTERNS = [
    re.compile(r"(?:到手价|券后价|补贴价|final(?:\s+price)?|after\s+coupon)\D{0,16}([1-9][0-9]{2,6}(?:\.[0-9]{1,2})?)", re.I),
    re.compile(r"([1-9][0-9]{2,6}(?:\.[0-9]{1,2})?)\s*(?:元)?\s*(?:到手|券后|final)", re.I),
]
LIST_PRICE_PATTERNS = [
    re.compile(r"(?:标价|原价|list(?:\s+price)?)\D{0,16}([1-9][0-9]{2,6}(?:\.[0-9]{1,2})?)", re.I),
]
DISCOUNT_PATTERNS = [
    re.compile(r"(?:优惠券|coupon)\D{0,12}([1-9][0-9]{1,5}(?:\.[0-9]{1,2})?)", re.I),
    re.compile(r"(?:补贴|subsidy)\D{0,12}([1-9][0-9]{1,5}(?:\.[0-9]{1,2})?)", re.I),
    re.compile(r"(?:满减|cross-store)\D{0,12}([1-9][0-9]{1,5}(?:\.[0-9]{1,2})?)", re.I),
]


@dataclass(frozen=True)
class ParsedPrice:
    list_price: float
    coupon_discount: float
    subsidy_discount: float
    cross_store_discount: float
    final_price: float


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def infer_brand(title_or_query: str) -> str:
    brands = [
        "Apple",
        "Samsung",
        "Xiaomi",
        "Huawei",
        "Sony",
        "Canon",
        "Nikon",
        "DJI",
        "Logitech",
        "Keychron",
        "Zeiss",
        "Sigma",
        "Tamron",
        "Leica",
        "Fujifilm",
        "Panasonic",
    ]
    lower = title_or_query.lower()
    for brand in brands:
        if brand.lower() in lower:
            return brand
    return title_or_query.split()[0] if title_or_query.split() else "Unknown"


def candidate_from_search_result(result: SearchResult, category: str) -> ProductCandidate:
    sku = clean_sku(result.title)
    return ProductCandidate(
        sku=sku,
        brand=infer_brand(sku),
        category=category,
        source_url=result.url,
        confidence=0.68 if result.snippet else 0.55,
    )


def clean_sku(title: str) -> str:
    for sep in [" | ", " - ", " – ", " — ", " :: ", " |"]:
        if sep in title:
            title = title.split(sep, 1)[0]
    title = re.sub(
        r"\b(official specifications|specifications manual|datasheet|white paper|说明书|白皮书|官网)\b.*$",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(r"\s+", " ", title).strip(" -–—_|")
    return title[:120] or "Unknown Product"


def extract_specs_from_text(text: str, source_url: str, category: str = "") -> list[OfficialSpec]:
    """Extract key-value specs from arbitrary product pages.

    When ``category`` matches a known template (see
    ``schemas.category_profile.CATEGORY_TEMPLATES``), labels are normalized
    onto that category's canonical 5-8 "hard slot" columns (e.g. "Focal
    Length" and "焦距" both collapse onto ``focal_length``) so the
    comparison matrix doesn't fragment into near-duplicate sparse columns.
    Unmodeled categories/labels fall back to a stable slugified name.
    """
    specs_by_name: dict[str, OfficialSpec] = {}
    for label, value in _extract_key_value_pairs(text):
        if SKIP_SPEC_LABELS.search(label):
            continue
        name = normalize_spec_name(label, category)
        if name in specs_by_name:
            continue
        specs_by_name[name] = OfficialSpec(
            name=name,
            value=clip(normalize_unit_text(value.strip()), 120),
            unit="",
            source_url=source_url,
        )

    slots = canonical_slots(category)
    slot_index = 0
    for measurement in _extract_measurements(text):
        while slot_index < len(slots) and slots[slot_index] in specs_by_name:
            slot_index += 1
        if slot_index >= len(slots):
            break
        slot = slots[slot_index]
        if any(spec.value == measurement for spec in specs_by_name.values()):
            continue
        specs_by_name[slot] = OfficialSpec(name=slot, value=measurement, unit="", source_url=source_url)
        slot_index += 1
    return list(specs_by_name.values())


def extract_specs_from_markup(markup: str, source_url: str, category: str = "") -> list[OfficialSpec]:
    text = sanitize_html(source_url, markup).rich_text
    specs_by_name = {spec.name: spec for spec in extract_specs_from_text(text, source_url, category)}

    for label, value in _extract_table_pairs(markup):
        if SKIP_SPEC_LABELS.search(label):
            continue
        name = normalize_spec_name(label, category)
        specs_by_name.setdefault(
            name,
            OfficialSpec(name=name, value=clip(normalize_unit_text(value), 120), unit="", source_url=source_url),
        )

    for label, value in _extract_json_ld_pairs(markup):
        if SKIP_SPEC_LABELS.search(label):
            continue
        name = normalize_spec_name(label, category)
        specs_by_name.setdefault(
            name,
            OfficialSpec(name=name, value=clip(normalize_unit_text(value), 120), unit="", source_url=source_url),
        )
    return list(specs_by_name.values())


def extract_detail_image_urls(markup: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in DETAIL_IMAGE_PATTERN.finditer(markup):
        url = match.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http") and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_desc_api_urls(markup: str, base_url: str = "") -> list[str]:
    urls: list[str] = []
    for match in DESC_API_PATTERN.finditer(markup):
        url = match.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/") and base_url:
            url = urljoin(base_url, url)
        if url.startswith("http"):
            urls.append(url)
    return list(dict.fromkeys(urls))


def infer_specs_from_sku(candidate: ProductCandidate) -> list[OfficialSpec]:
    """Keyword fallback only — no category-specific SKU parsing."""
    return []


def build_evidence(platform: str, url: str, author: str, locator: str, excerpt: str, confidence: float) -> EvidenceItem:
    return EvidenceItem(
        platform=platform,
        url=url,
        author=author,
        locator=locator,
        captured_at=now_iso(),
        excerpt=clip(excerpt, 420),
        confidence=confidence,
    )


def evidence_from_page(platform: str, url: str, markup: str, confidence: float = 0.62) -> list[EvidenceItem]:
    text = sanitize_html(url, markup).rich_text
    evidence: list[EvidenceItem] = []
    for index, pattern in enumerate(NEGATIVE_PATTERNS):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 220)
        excerpt = text[start:end]
        if not any(re.search(review_pattern, excerpt, re.I) for review_pattern in REVIEW_PATTERNS):
            continue
        evidence.append(
            build_evidence(
                platform=platform,
                url=url,
                author=domain_author(url),
                locator=f"text-match-{index + 1}",
                excerpt=excerpt,
                confidence=confidence,
            )
        )
    return dedupe_evidence(evidence)


def evidence_from_search_result(platform: str, result: SearchResult, confidence: float = 0.55) -> EvidenceItem | None:
    combined = f"{result.title}. {result.snippet}"
    if not result.url.startswith("http"):
        return None
    if not any(re.search(pattern, combined, re.I) for pattern in NEGATIVE_PATTERNS):
        return None
    return build_evidence(platform, result.url, domain_author(result.url), "search-result", combined, confidence)


def extract_price(text: str) -> ParsedPrice | None:
    final_matches = first_pattern_numbers(FINAL_PRICE_PATTERNS, text)
    list_matches = first_pattern_numbers(LIST_PRICE_PATTERNS, text)
    discount_matches = [first_pattern_numbers([pattern], text) for pattern in DISCOUNT_PATTERNS]
    numbers = [float(match.group(1)) for match in PRICE_PATTERN.finditer(text)]
    numbers = [number for number in numbers if is_plausible_price(number)]
    if not numbers:
        return None
    final_price = final_matches[0] if final_matches else min(numbers)
    list_price = list_matches[0] if list_matches else max(numbers + [final_price])
    discount = max(0.0, list_price - final_price)
    explicit_discounts = [matches[0] for matches in discount_matches if matches]
    if explicit_discounts:
        coupon = explicit_discounts[0] if len(explicit_discounts) > 0 else 0
        subsidy = explicit_discounts[1] if len(explicit_discounts) > 1 else 0
        cross_store = explicit_discounts[2] if len(explicit_discounts) > 2 else max(0.0, discount - coupon - subsidy)
    else:
        coupon = round(discount * 0.35, 2)
        subsidy = round(discount * 0.45, 2)
        cross_store = round(discount - coupon - subsidy, 2)
    return ParsedPrice(list_price, coupon, subsidy, cross_store, final_price)


def first_pattern_numbers(patterns: list[re.Pattern[str]], text: str) -> list[float]:
    values: list[float] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            number = float(match.group(1))
            if is_plausible_price(number):
                values.append(number)
        if values:
            return values
    return values


def is_plausible_price(number: float) -> bool:
    return 100 <= number <= 1_000_000 and not (1900 <= number <= 2099)


def domain_author(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or "web"


def platform_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "bilibili" in host:
        return "Bilibili"
    if "youtube" in host or "youtu.be" in host:
        return "YouTube"
    if "chiphell" in host:
        return "Chiphell"
    if "reddit" in host:
        return "Reddit"
    if "jd." in host or "jd.com" in host:
        return "JD"
    if "taobao" in host or "tmall" in host:
        return "Taobao/Tmall"
    return host or "Web"


def dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str]] = set()
    output: list[EvidenceItem] = []
    for item in items:
        key = (item.url, item.excerpt[:80])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _extract_key_value_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in KEY_VALUE_SPEC_PATTERN.finditer(text):
        label = match.group(1).strip()
        value = match.group(2).strip()
        if len(label) < 2 or len(value) < 1 or SKIP_SPEC_LABELS.search(label):
            continue
        pairs.append((label, value))
    return pairs


def _extract_measurements(text: str) -> list[str]:
    seen: set[str] = set()
    measurements: list[str] = []
    for match in MEASUREMENT_PATTERN.finditer(text):
        value = normalize_unit_text(match.group(1).strip())
        if value not in seen:
            seen.add(value)
            measurements.append(value)
    return measurements


def normalize_unit_text(value: str) -> str:
    normalized = normalize_whitespace(value)
    substitutions = (
        ("毫安时", "mAh"),
        ("瓦时", "Wh"),
        ("千瓦时", "kWh"),
        ("毫米", "mm"),
        ("厘米", "cm"),
        ("米", "m"),
        ("千克", "kg"),
        ("克", "g"),
        ("英寸", "inch"),
        ("小时", "h"),
        ("分钟", "min"),
    )
    for source, target in substitutions:
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"([0-9])\s*(mm|cm|m|g|kg|mah|wh|kwh|inch|w|h|min)\b", r"\1 \2", normalized, flags=re.I)
    normalized = re.sub(r"\bmah\b", "mAh", normalized)
    normalized = re.sub(r"\bwh\b", "Wh", normalized)
    normalized = re.sub(r"\bkwh\b", "kWh", normalized)
    return normalized


def _extract_table_pairs(markup: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in TABLE_ROW_PATTERN.findall(markup):
        cells = [normalize_whitespace(strip_tags(cell)) for cell in TABLE_CELL_PATTERN.findall(row)]
        cells = [cell for cell in cells if cell]
        if len(cells) >= 2:
            pairs.append((cells[0], cells[1]))
    return pairs


def _extract_json_ld_pairs(markup: str) -> list[tuple[str, str]]:
    page = sanitize_html("", markup)
    pairs: list[tuple[str, str]] = []
    for obj in page.json_ld:
        pairs.extend(_walk_dict_pairs(obj))
    return pairs


def _walk_dict_pairs(node: object, parent: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            nested_key = f"{parent}_{key}" if parent else str(key)
            if isinstance(value, (dict, list)):
                pairs.extend(_walk_dict_pairs(value, nested_key))
            elif isinstance(value, (str, int, float)) and len(str(value)) <= 120:
                pairs.append((nested_key, str(value)))
    elif isinstance(node, list):
        for item in node:
            pairs.extend(_walk_dict_pairs(item, parent))
    return pairs
