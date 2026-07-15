from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

from collectors.page_sanitize import sanitize_html
from collectors.http import SearchResult, clip, normalize_whitespace, strip_tags
from schemas import EvidenceItem, OfficialSpec, ProductCandidate
from schemas.category_profile import (
    DynamicCategoryProfile,
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
    # Lens (Sony-style heading + value without colon)
    r"Focal Length(?:\s*\([^)]*\))?|"
    r"Maximum aperture(?:\s*\([^)]*\))?|"
    r"Minimum Aperture(?:\s*\([^)]*\))?|"
    r"Filter Diameter(?:\s*\([^)]*\))?|"
    r"Minimum Focus Distance|"
    r"Mount|"
    r"焦距|最大光圈|滤镜口径|最近对焦距离|卡口|"
    # Cross-category manufacturer pages
    r"Weight|Battery(?:\s+Capacity|\s+Life)?|Screen Size|Refresh Rate|"
    r"RAM|Storage|CPU|GPU|Impedance|Driver Size|"
    r"重量|电池容量|续航|屏幕尺寸|刷新率|内存|存储|处理器|显卡|阻抗|单元直径"
    r")\s+("
    r"Sony\s+E-mount|"
    r"[0-9]+(?:\.[0-9]+)?\s+oz\s*\(\s*[0-9]+(?:\.[0-9]+)?\s*g\s*\)|"
    r"[0-9]+(?:\.[0-9]+)?\s+ft\s*\(\s*[0-9]+(?:\.[0-9]+)?\s*m\s*\)|"
    r"[0-9]+(?:\.[0-9]+)?(?:\s*(?:mm|cm|m|g|kg|oz|ft|inch|英寸|hz|mah|wh|gb|tb|ohm|Ω|小时|h)\b)?"
    r")",
    re.I,
)
MEASUREMENT_PATTERN = re.compile(
    r"\b([0-9]+(?:\.[0-9]+)?\s*(?:mm|cm|m|g|kg|gb|tb|hz|w|mah|wh|ohm|Ω|inch|英寸|克|千克|毫米|厘米|米|小时|%))\b",
    re.I,
)
APERTURE_PATTERN = re.compile(r"\bf\s*/\s*([0-9]+(?:\.[0-9]+)?)\b", re.I)
TABLE_ROW_PATTERN = re.compile(r"<tr[^>]*>\s*(.*?)\s*</tr>", re.I | re.S)
TABLE_CELL_PATTERN = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
DETAIL_IMAGE_PATTERN = re.compile(
    r"""(?:src|data-src|data-lazyload|data-lazy-img|data-original|original|file-url)\s*=\s*["']([^"']+\.(?:jpg|jpeg|png|webp)(?:\?[^"']*)?)["']""",
    re.I,
)
# JSON / script blobs often embed CDN paths without HTML attributes.
DETAIL_IMAGE_JSON_PATTERN = re.compile(
    r"""["'](?:image(?:Url|Path|url)?|img(?:Url|url)?|pic(?:Url|url)?|src)["']\s*:\s*["']((?:https?:)?//[^"']+\.(?:jpg|jpeg|png|webp)(?:\?[^"']*)?)["']""",
    re.I,
)
# JD / Ali CDN paths sometimes omit a file extension in the path segment.
DETAIL_IMAGE_CDN_PATTERN = re.compile(
    r"""((?:https?:)?//(?:img\d*\.360buyimg\.com|[^"'\\\s]*\.alicdn\.com)/[^"'\\\s<>]+(?:\.(?:jpg|jpeg|png|webp)|/[ns]\d+/|/imgextra/)[^"'\\\s<>]*)""",
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
    "battery_capacity": re.compile(r"\b(?:mah|wh|kwh)\b", re.I),
    "battery_life": re.compile(r"(?:h|hour|小时|mah)", re.I),
    "ram": re.compile(r"\b(?:gb|tb)\b", re.I),
    "storage": re.compile(r"\b(?:gb|tb)\b", re.I),
    "refresh_rate": re.compile(r"\bhz\b", re.I),
    "impedance": re.compile(r"(?:ohm|Ω|欧)", re.I),
    "driver_size": re.compile(r"\bmm\b", re.I),
    "max_flight_time": re.compile(r"(?:min|分钟|h|hour|小时)", re.I),
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
    compact = re.sub(r"[^a-z0-9]", "", (title_or_query or "").lower())
    if compact.startswith("sel") and re.match(r"^sel\d+", compact):
        return "Sony"
    # Chinese aliases first so "罗技 G304" → Logitech, not "罗技".
    brand_aliases: list[tuple[str, str]] = [
        ("罗技", "Logitech"),
        ("logitech", "Logitech"),
        ("雷蛇", "Razer"),
        ("razer", "Razer"),
        ("雷柏", "Rapoo"),
        ("rapoo", "Rapoo"),
        ("赛睿", "SteelSeries"),
        ("steelseries", "SteelSeries"),
        ("漫步者", "Edifier"),
        ("edifier", "Edifier"),
        ("樱桃", "Cherry"),
        ("cherry", "Cherry"),
        ("keychron", "Keychron"),
        ("苹果", "Apple"),
        ("apple", "Apple"),
        ("三星", "Samsung"),
        ("samsung", "Samsung"),
        ("小米", "Xiaomi"),
        ("xiaomi", "Xiaomi"),
        ("华为", "Huawei"),
        ("huawei", "Huawei"),
        ("索尼", "Sony"),
        ("sony", "Sony"),
        ("微软", "Microsoft"),
        ("microsoft", "Microsoft"),
        ("佳能", "Canon"),
        ("canon", "Canon"),
        ("尼康", "Nikon"),
        ("nikon", "Nikon"),
        ("大疆", "DJI"),
        ("dji", "DJI"),
        ("蔡司", "Zeiss"),
        ("zeiss", "Zeiss"),
        ("适马", "Sigma"),
        ("sigma", "Sigma"),
        ("腾龙", "Tamron"),
        ("tamron", "Tamron"),
        ("徕卡", "Leica"),
        ("leica", "Leica"),
        ("富士", "Fujifilm"),
        ("fujifilm", "Fujifilm"),
        ("松下", "Panasonic"),
        ("panasonic", "Panasonic"),
    ]
    lower = (title_or_query or "").lower()
    for needle, brand in brand_aliases:
        if needle in lower:
            return brand
    # Never treat a Chinese headline / whole sentence as a "brand".
    token = (title_or_query or "").split()[0] if (title_or_query or "").split() else ""
    if not token or len(token) > 24 or re.search(r"[？?！!，,。：:]", token):
        return "Unknown"
    if len(re.findall(r"[\u4e00-\u9fff]", token)) >= 6 and not primary_model_code(token):
        return "Unknown"
    return token or "Unknown"


def sku_identity_key(sku: str) -> str:
    """Casefold alnum key for dedupe (no category-specific rewrites)."""
    return "".join(ch for ch in (sku or "").casefold() if ch.isalnum())


_CATEGORY_URL_HINTS = (
    "/shop/c/",
    "/category/",
    "/categories/",
    "/list/",
    "/search",
    "list.jd.com",
    "search.jd.com",
    "s.taobao.com",
    "list.tmall.com",
)
_PRODUCT_URL_HINTS = (
    "/shop/p/",
    "item.jd.com",
    "item.m.jd.com",
    "/product/",
    "detail.tmall.com",
    "detail.taobao.com",
    "item.taobao.com",
)


def is_category_or_list_url(url: str) -> bool:
    lower = (url or "").lower()
    if not lower:
        return False
    return any(hint in lower for hint in _CATEGORY_URL_HINTS)


def is_product_detail_url(url: str) -> bool:
    lower = (url or "").lower()
    if not lower or is_category_or_list_url(lower):
        return False
    return any(hint in lower for hint in _PRODUCT_URL_HINTS)


def is_concrete_product_sku(sku: str) -> bool:
    """Thin structural check. Discovery semantics are handled by the LLM normalizer."""
    text = (sku or "").strip()
    if not text or text == "Unknown Product":
        return False
    return 2 <= len(text) <= 80


def candidate_from_search_result(result: SearchResult, category: str) -> ProductCandidate:
    """Legacy helper — prefer AI discovery; do not treat headlines as SKUs."""
    sku = clean_sku(result.title)
    return ProductCandidate(
        sku=sku if is_concrete_product_sku(sku) else "Unknown Product",
        brand=infer_brand(sku),
        category=category,
        source_url=result.url,
        confidence=0.35,
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


def extract_specs_from_text(
    text: str,
    source_url: str,
    category: str = "",
    profile: DynamicCategoryProfile | None = None,
) -> list[OfficialSpec]:
    """Extract key-value specs from arbitrary product pages.

    When a JIT ``profile`` is provided, labels collapse onto that profile's
    5-8 hard slots via aliases. Otherwise names are slugified.
    """
    specs_by_name: dict[str, OfficialSpec] = {}
    for label, value in _extract_key_value_pairs(text):
        _store_spec(specs_by_name, label, value, source_url, category, profile)
    for label, value in _extract_adjacent_spec_pairs(text):
        _store_spec(specs_by_name, label, value, source_url, category, profile)

    slots = canonical_slots(category, profile=profile)
    if "max_aperture" in slots and "max_aperture" not in specs_by_name:
        aperture = APERTURE_PATTERN.search(text)
        if aperture:
            specs_by_name["max_aperture"] = OfficialSpec(
                name="max_aperture",
                value=f"f/{aperture.group(1)}",
                unit="",
                source_url=source_url,
            )

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
    profile: DynamicCategoryProfile | None = None,
) -> None:
    if SKIP_SPEC_LABELS.search(label):
        return
    if re.search(r"equivalent|aps-c|35\s*mm\s*equivalent", label, re.I):
        return
    # Never map minimum aperture -> max_aperture via alias.
    if re.search(r"minimum\s+aperture|最小光圈", label, re.I):
        return
    name = normalize_spec_name(label, category, profile=profile)
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


def extract_specs_from_markup(
    markup: str,
    source_url: str,
    category: str = "",
    profile: DynamicCategoryProfile | None = None,
) -> list[OfficialSpec]:
    text = sanitize_html(source_url, markup).rich_text
    specs_by_name = {
        spec.name: spec for spec in extract_specs_from_text(text, source_url, category, profile=profile)
    }

    for label, value in _extract_table_pairs(markup):
        if SKIP_SPEC_LABELS.search(label):
            continue
        name = normalize_spec_name(label, category, profile=profile)
        specs_by_name.setdefault(
            name,
            OfficialSpec(name=name, value=clip(normalize_unit_text(value), 120), unit="", source_url=source_url),
        )

    for label, value in _extract_json_ld_pairs(markup):
        if SKIP_SPEC_LABELS.search(label):
            continue
        name = normalize_spec_name(label, category, profile=profile)
        specs_by_name.setdefault(
            name,
            OfficialSpec(name=name, value=clip(normalize_unit_text(value), 120), unit="", source_url=source_url),
        )
    return list(specs_by_name.values())


def extract_detail_image_urls(markup: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    if not markup:
        return []
    patterns = (DETAIL_IMAGE_PATTERN, DETAIL_IMAGE_JSON_PATTERN, DETAIL_IMAGE_CDN_PATTERN)
    for pattern in patterns:
        for match in pattern.finditer(markup):
            url = _normalize_detail_image_url(match.group(1))
            if not url or url in seen or _is_noise_detail_image_url(url):
                continue
            seen.add(url)
            urls.append(url)
    return rank_detail_image_urls(urls)


def rank_detail_image_urls(urls: list[str]) -> list[str]:
    """Prefer packaging/spec/detail graphics over icons and tiny thumbnails."""

    def score(url: str) -> tuple[int, int]:
        lower = url.lower()
        points = 0
        for hint in (
            "参数",
            "规格",
            "包装",
            "detail",
            "desc",
            "spec",
            "package",
            "imgextra",
            "/n0/",
            "/n1/",
            "/n12/",
            "_param_",
            "param_fallback",
        ):
            if hint in lower:
                points += 4
        if lower.startswith("file:"):
            points += 3
        for hint in ("800x", "1000x", "1200x", "1500x", "790x", "750x"):
            if hint in lower:
                points += 2
        for hint in (
            "avatar",
            "sprite",
            "placeholder",
            "blank",
            "1x1",
            "pixel",
            "/n5/",
            "/n7/",
            "s40x40",
            "s50x50",
            "s60x60",
            "icon",
            "logo",
        ):
            if hint in lower:
                points -= 6
        # Prefer longer paths (often real assets) as a weak tie-breaker.
        return (points, min(len(url), 400))

    return sorted(dict.fromkeys(urls), key=score, reverse=True)


def _normalize_detail_image_url(raw: str) -> str:
    url = (raw or "").strip().strip("\\")
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        return ""
    return url.split()[0].rstrip("\\\",'")


def _is_noise_detail_image_url(url: str) -> bool:
    lower = url.lower()
    if any(ext in lower for ext in (".gif", ".svg", ".ico")):
        return True
    if any(hint in lower for hint in ("data:image", "about:blank")):
        return True
    # Extremely small JD thumbs / tracking pixels (s50x50.jpg or s50x50_jfs).
    if re.search(r"(?:^|/)(?:s|_)?(?:[1-6]0)x(?:[1-6]0)(?:[._/]|$)", lower):
        return True
    return False


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
    """Deterministic fallback when page extraction is thin (Sony SEL codes)."""
    compact = re.sub(r"[^a-z0-9]", "", (candidate.sku or "").lower())
    match = _SONY_SEL_RE.match(compact)
    if not match:
        return []
    focal, aperture_raw, grade = match.group(1), match.group(2), (match.group(3) or "").lower()
    if len(aperture_raw) == 2 and aperture_raw[0] in "12":
        aperture = f"{aperture_raw[0]}.{aperture_raw[1]}"
    else:
        aperture = aperture_raw
    source_url = candidate.source_url or ""
    specs = [
        OfficialSpec(name="focal_length", value=f"{focal}mm", unit="", source_url=source_url),
        OfficialSpec(name="max_aperture", value=f"f/{aperture}", unit="", source_url=source_url),
        OfficialSpec(name="mount", value="Sony E-mount", unit="", source_url=source_url),
    ]
    if grade:
        specs.append(OfficialSpec(name="product_line", value=grade.upper(), unit="", source_url=source_url))
    return specs


_FORUM_CHROME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"积分\s*\d+"),
    re.compile(r"当前离线"),
    re.compile(r"回复\s*举报"),
    re.compile(r"只看该作者"),
    re.compile(r"发表于\s*\d{4}-\d"),
    re.compile(r"#\s*\d+\s*\|"),
    re.compile(r"楼主\s*\|"),
    re.compile(r"积分\s+\d+"),
)
_FORUM_CHROME_MARKERS: tuple[str, ...] = (
    "积分",
    "回复",
    "举报",
    "当前离线",
    "只看该作者",
    "发表于",
)


def clean_evidence_excerpt(text: str) -> str:
    cleaned = text or ""
    for pattern in _FORUM_CHROME_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def forum_chrome_ratio(text: str) -> float:
    if not text:
        return 0.0
    hits = sum(1 for marker in _FORUM_CHROME_MARKERS if marker in text)
    return hits / len(_FORUM_CHROME_MARKERS)


def evidence_conflicts_with_sku(sku: str, *texts: str) -> bool:
    if not sku or not sku.strip():
        return False
    blob = " ".join(part for part in texts if part).lower()
    if not blob.strip():
        return False
    compact_sku = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", sku.lower())
    compact_blob = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", blob)
    if len(compact_sku) >= 5 and compact_sku in compact_blob:
        return False
    if not _SONY_SEL_RE.match(compact_sku):
        return False
    has_sony = any(token in blob for token in ("sony", "索尼", "fe ", "fe-"))
    if has_sony:
        return False
    competitor_markers = (
        "canon",
        "nikon",
        "sigma",
        "tamron",
        "leica",
        "fujifilm",
        "富士",
        " rf ",
        "canon rf",
        "ef 50",
        "ef50",
        "z 50",
        "z50",
    )
    return any(marker in blob for marker in competitor_markers)


def build_evidence(platform: str, url: str, author: str, locator: str, excerpt: str, confidence: float) -> EvidenceItem:
    cleaned = clean_evidence_excerpt(excerpt)
    if forum_chrome_ratio(cleaned) >= 0.45:
        cleaned = ""
    return EvidenceItem(
        platform=platform,
        url=url,
        author=author,
        locator=locator,
        captured_at=now_iso(),
        excerpt=clip(cleaned or excerpt, 420),
        confidence=confidence,
    )


_SKU_STOP_WORDS = {
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
    "pro",
    "max",
    "mini",
    "plus",
    "ultra",
    "mount",
    "series",
    "edition",
}
_MODEL_CODE_RE = re.compile(
    r"[a-z]{2,}\d+[a-z0-9]*|\d+[a-z]{2,}[a-z0-9]*|[a-z]\d{2,4}[a-z]?",
    re.I,
)
_FOCAL_OR_APERTURE_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?(?:mm)?$|^f/?\d+(?:\.\d+)?$", re.I)
# Sony E-mount lens codes: SEL50F12GM → 50mm + F1.2 + GM (common on CN marketplaces).
_SONY_SEL_RE = re.compile(r"^sel(\d+)f(\d+)(gm|g|oss|za)?$", re.I)


def primary_model_code(sku: str) -> str:
    """Best alphanumeric model token from a SKU / marketing name."""
    best = ""
    for token in _MODEL_CODE_RE.findall(sku or ""):
        # Accept G304 / K380 (4 chars) and longer codes; skip tiny noise.
        if len(token) < 3 or len(token) <= len(best):
            continue
        # Focal / aperture fragments are not model codes (50mm, f1.2).
        if _FOCAL_OR_APERTURE_TOKEN_RE.match(token):
            continue
        if token.lower().endswith("mm") and token[:-2].isdigit():
            continue
        best = token
    return best


def sku_search_phrase(sku: str) -> str:
    """Quote distinctive model codes / names so DDG prefers exact product hits."""
    cleaned = (sku or "").strip()
    if not cleaned:
        return cleaned
    model = primary_model_code(cleaned)
    if model and len(model) >= 5:
        compact = re.sub(r"[^a-z0-9]", "", cleaned.lower())
        if model.lower() == compact:
            return f'"{model}"'
        return f'"{model}" {cleaned}'
    if " " in cleaned and len(cleaned) <= 100:
        return f'"{cleaned}"'
    return cleaned


def sku_marketplace_aliases(sku: str) -> list[str]:
    """Phrases that often replace a raw model code on JD/Tmall/review titles."""
    compact = re.sub(r"[^a-z0-9]", "", (sku or "").lower())
    match = _SONY_SEL_RE.match(compact)
    if not match:
        return []
    focal, aperture_raw, grade = match.group(1), match.group(2), (match.group(3) or "").lower()
    if len(aperture_raw) == 2 and aperture_raw[0] in "12":
        aperture = f"{aperture_raw[0]}.{aperture_raw[1]}"
    else:
        aperture = aperture_raw
    aliases = [
        f"{focal}mm",
        f"f/{aperture}",
        f"f{aperture}",
        f"{focal}mm f/{aperture}",
        f"{focal}mm f{aperture}",
    ]
    if grade:
        aliases.extend(
            [
                grade,
                f"{focal}mm f/{aperture} {grade}",
                f"{focal}mm f{aperture} {grade}",
                f"fe {focal}mm f/{aperture} {grade}",
            ]
        )
    return aliases


def evidence_mentions_sku(sku: str, *texts: str) -> bool:
    """True when text clearly refers to the target SKU / model code."""
    if not sku or not sku.strip():
        return True
    if evidence_conflicts_with_sku(sku, *texts):
        return False
    blob = " ".join(part for part in texts if part).lower()
    if not blob.strip():
        return False
    compact_sku = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", sku.lower())
    compact_blob = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", blob)
    if len(compact_sku) >= 5 and compact_sku in compact_blob:
        return True

    model_tokens = [token.lower() for token in _MODEL_CODE_RE.findall(sku) if len(token) >= 3]
    if model_tokens:
        if any(token in compact_blob for token in model_tokens):
            return True
        # Marketplace titles often omit SEL… codes but keep focal + aperture + grade.
        aliases = [alias.lower() for alias in sku_marketplace_aliases(sku)]
        if aliases:
            alias_hits = sum(1 for alias in aliases if alias in blob or alias.replace(" ", "") in compact_blob)
            strong = [a for a in aliases if "mm" in a and ("f/" in a or "f1" in a or "f2" in a)]
            if any(item in blob or item.replace(" ", "") in compact_blob for item in strong):
                if _SONY_SEL_RE.match(compact_sku):
                    if not any(token in blob for token in ("sony", "索尼", "fe ", "fe-")):
                        return False
                return True
            if alias_hits >= 3:
                return True
        # Model-code SKUs must not match on weak shared words alone (e.g. "50mm").
        return False

    words = [
        word
        for word in re.findall(r"[a-z0-9\u4e00-\u9fff]+", sku.lower())
        if len(word) >= 3
        and word not in _SKU_STOP_WORDS
        and not _FOCAL_OR_APERTURE_TOKEN_RE.match(word)
    ]
    if not words:
        return False
    hits = sum(1 for word in words if word in blob or word in compact_blob)
    if len(words) >= 2:
        if hits >= 2:
            return True
        # Brand + the SKU's own focal length is enough for marketplace titles.
        if hits >= 1:
            for focal in re.findall(r"\b(\d+(?:\.\d+)?)\s*mm\b", sku.lower()):
                token = f"{focal}mm"
                if token in compact_blob or token in blob.replace(" ", ""):
                    return True
        return False
    return hits >= 1 and len(words[0]) >= 4


def page_matches_sku(sku: str, *, title: str = "", text: str = "", url: str = "") -> bool:
    """Post-fetch identity check: prefer title, then leading body text."""
    if not sku or not sku.strip():
        return True
    if title and evidence_mentions_sku(sku, title, url):
        return True
    head = (text or "")[:2500]
    return evidence_mentions_sku(sku, title, head, url)


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
    # Accept defect OR review wording (same-excerpt double gate was starving live pages).
    gate_patterns = list(NEGATIVE_PATTERNS) + list(REVIEW_PATTERNS)
    for index, pattern in enumerate(gate_patterns):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 220)
        excerpt = clean_evidence_excerpt(text[start:end])
        if len(excerpt.strip()) < 24:
            continue
        if forum_chrome_ratio(excerpt) >= 0.5:
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
    if not any(re.search(pattern, combined, re.I) for pattern in NEGATIVE_PATTERNS) and not any(
        re.search(pattern, combined, re.I) for pattern in REVIEW_PATTERNS
    ):
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
