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
# Sony support / many manufacturer pages use heading + value without a colon.
ADJACENT_SPEC_PATTERN = re.compile(
    r"\b("
    r"Focal Length(?:\s*\([^)]*\))?|"
    r"Maximum aperture(?:\s*\([^)]*\))?|"
    r"Minimum Aperture(?:\s*\([^)]*\))?|"
    r"Filter Diameter(?:\s*\([^)]*\))?|"
    r"Minimum Focus Distance|"
    r"Weight|"
    r"Mount|"
    r"焦距|最大光圈|滤镜口径|最近对焦距离|重量|卡口"
    r")\s+("
    r"Sony\s+E-mount|"
    r"[0-9]+(?:\.[0-9]+)?\s+oz\s*\(\s*[0-9]+(?:\.[0-9]+)?\s*g\s*\)|"
    r"[0-9]+(?:\.[0-9]+)?\s+ft\s*\(\s*[0-9]+(?:\.[0-9]+)?\s*m\s*\)|"
    r"[0-9]+(?:\.[0-9]+)?(?:\s*(?:mm|cm|m|g|kg|oz|ft)\b)?"
    r")",
    re.I,
)
MEASUREMENT_PATTERN = re.compile(
    r"\b([0-9]+(?:\.[0-9]+)?\s*(?:mm|cm|m|g|kg|gb|tb|hz|w|mah|inch|英寸|克|千克|毫米|厘米|米|%))\b",
    re.I,
)
APERTURE_PATTERN = re.compile(r"\bf\s*/\s*([0-9]+(?:\.[0-9]+)?)\b", re.I)
TABLE_ROW_PATTERN = re.compile(r"<tr[^>]*>\s*(.*?)\s*</tr>", re.I | re.S)
TABLE_CELL_PATTERN = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
DETAIL_IMAGE_PATTERN = re.compile(
    r"""(?:src|data-src|data-lazyload|original|file-url)\s*=\s*["']([^"']+\.(?:jpg|jpeg|png|webp)(?:\?[^"']*)?)["']""",
    re.I,
)
DESC_API_PATTERN = re.compile(r"""["']((?:https?:)?//[^"']*(?:getdesc|desc|description)[^"']*)["']""", re.I)
SKIP_SPEC_LABELS = re.compile(
    r"("
    r"copyright|cookie|login|javascript|function|window|price|价格|购买|cart|subscribe|"
    r"违法|举报|维权|许可证|经营许可|京东首页|可信网站|扫黄|适老化|消费者维权|"
    r"网络警察|互联网举报|免费注册|我的订单|增值电信"
    r")",
    re.I,
)
# Measurement backfill must match slot semantics — bare "0.4 m" must not fill max_aperture.
_SLOT_VALUE_COMPAT: dict[str, re.Pattern[str]] = {
    "focal_length": re.compile(r"\bmm\b", re.I),
    "max_aperture": re.compile(r"^f\s*/\s*[0-9]+(?:\.[0-9]+)?$", re.I),
    "optical_structure": re.compile(r"(?:group|element|组|片|/)", re.I),
    "filter_diameter": re.compile(r"\bmm\b", re.I),
    "weight": re.compile(r"\b(?:g|kg)\b", re.I),
    "min_focus_distance": re.compile(r"\b(?:m|cm|mm)\b", re.I),
    "screen_size": re.compile(r"\b(?:inch|英寸|mm)\b", re.I),
    "battery_capacity": re.compile(r"\b(?:mah|wh|kwh|mah)\b", re.I),
    "ram": re.compile(r"\b(?:gb|tb)\b", re.I),
    "storage": re.compile(r"\b(?:gb|tb)\b", re.I),
}

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
        _store_spec(specs_by_name, label, value, source_url, category)
    for label, value in _extract_adjacent_spec_pairs(text):
        _store_spec(specs_by_name, label, value, source_url, category)

    # Prefer explicit f-numbers for aperture before any measurement backfill.
    if "max_aperture" not in specs_by_name:
        aperture = APERTURE_PATTERN.search(text)
        if aperture:
            specs_by_name["max_aperture"] = OfficialSpec(
                name="max_aperture",
                value=f"f/{aperture.group(1)}",
                unit="",
                source_url=source_url,
            )

    slots = canonical_slots(category)
    for measurement in _extract_measurements(text):
        if any(spec.value == measurement for spec in specs_by_name.values()):
            continue
        for slot in slots:
            if slot in specs_by_name:
                continue
            if not _measurement_fits_slot(slot, measurement):
                continue
            specs_by_name[slot] = OfficialSpec(name=slot, value=measurement, unit="", source_url=source_url)
            break
    return list(specs_by_name.values())


def _store_spec(
    specs_by_name: dict[str, OfficialSpec],
    label: str,
    value: str,
    source_url: str,
    category: str,
) -> None:
    if SKIP_SPEC_LABELS.search(label):
        return
    # Prefer full-frame focal length over APS-C equivalent labels.
    if re.search(r"equivalent|aps-c|35\s*mm\s*equivalent", label, re.I):
        return
    # Sony lists both max and min aperture; never map minimum aperture -> max_aperture.
    if re.search(r"minimum\s+aperture|最小光圈", label, re.I):
        return
    name = normalize_spec_name(label, category)
    if name in specs_by_name:
        return
    cleaned = normalize_unit_text(_prefer_metric_value(value.strip()))
    if name in {"max_aperture", "min_aperture"} and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cleaned):
        cleaned = f"f/{cleaned}"
    if name in {"focal_length", "filter_diameter"} and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cleaned):
        cleaned = f"{cleaned} mm"
    specs_by_name[name] = OfficialSpec(
        name=name,
        value=clip(cleaned, 120),
        unit="",
        source_url=source_url,
    )


def _prefer_metric_value(value: str) -> str:
    """Prefer metric parenthetical values: '27.5 oz (778 g)' -> '778 g'."""
    match = re.search(r"\(([0-9]+(?:\.[0-9]+)?\s*(?:mm|cm|m|g|kg))\)", value, re.I)
    if match:
        return match.group(1)
    return value


def _extract_adjacent_spec_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in ADJACENT_SPEC_PATTERN.finditer(text):
        label = match.group(1).strip()
        value = match.group(2).strip()
        if len(label) < 2 or len(value) < 1:
            continue
        pairs.append((label, value))
    return pairs


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


def evidence_mentions_sku(sku: str, *texts: str) -> bool:
    """True when text clearly refers to the target SKU / model code."""
    if not sku or not sku.strip():
        return True
    blob = " ".join(part for part in texts if part).lower()
    if not blob.strip():
        return False
    compact_sku = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", sku.lower())
    compact_blob = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", blob)
    if len(compact_sku) >= 5 and compact_sku in compact_blob:
        return True
    # Alphanumeric model tokens (SEL50F12GM, BV1xx, etc.)
    for token in re.findall(r"[a-z]{2,}\d+[a-z0-9]*|\d+[a-z]+[a-z0-9]*", sku.lower()):
        if len(token) >= 5 and token in compact_blob:
            return True
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "lens",
        "镜头",
        "official",
        "review",
        "评测",
        "mm",
        "gm",
        "fe",
    }
    words = [
        word
        for word in re.findall(r"[a-z0-9\u4e00-\u9fff]+", sku.lower())
        if len(word) >= 3 and word not in stop
    ]
    if not words:
        return False
    hits = sum(1 for word in words if word in blob or word in compact_blob)
    return hits >= 2 if len(words) >= 2 else hits >= 1


def evidence_from_page(
    platform: str,
    url: str,
    markup: str,
    confidence: float = 0.62,
    *,
    sku: str = "",
) -> list[EvidenceItem]:
    text = sanitize_html(url, markup).rich_text
    if sku and not evidence_mentions_sku(sku, text[:4000], url):
        return []
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


def evidence_from_search_result(
    platform: str,
    result: SearchResult,
    confidence: float = 0.55,
    *,
    sku: str = "",
) -> EvidenceItem | None:
    combined = f"{result.title}. {result.snippet}"
    if not result.url.startswith("http"):
        return None
    if sku and not evidence_mentions_sku(sku, combined, result.url):
        return None
    if not any(re.search(pattern, combined, re.I) for pattern in NEGATIVE_PATTERNS):
        return None
    return build_evidence(platform, result.url, domain_author(result.url), "search-result", combined, confidence)


def _measurement_fits_slot(slot: str, measurement: str) -> bool:
    pattern = _SLOT_VALUE_COMPAT.get(slot)
    if pattern is None:
        # Unknown / generic slots: only backfill when no unit constraints exist.
        return slot.startswith("parameter_")
    return bool(pattern.search(measurement))


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
