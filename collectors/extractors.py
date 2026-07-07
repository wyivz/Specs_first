from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from collectors.page_sanitize import sanitize_html
from collectors.http import SearchResult, clip
from schemas import EvidenceItem, OfficialSpec, ProductCandidate


SPEC_PATTERNS = {
    "focal_length": [
        r"(?:focal length|焦距)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*mm)",
        r"\b([0-9]{2,3}\s*mm)\b",
    ],
    "max_aperture": [
        r"(?:maximum aperture|最大光圈|aperture)\s*[:：]?\s*(f/?\s*[0-9]+(?:\.[0-9]+)?)",
        r"\b(f/?\s*[0-9]+(?:\.[0-9]+)?)\b",
    ],
    "weight": [
        r"(?:weight|重量)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*(?:g|kg|克))",
    ],
    "optical_structure": [
        r"(?:optical (?:construction|structure)|lens construction|镜头结构|光学结构)\s*[:：]?\s*([0-9]+\s*(?:groups?|组)\s*/?\s*[0-9]+\s*(?:elements?|片))",
        r"([0-9]+\s*组\s*[0-9]+\s*片)",
    ],
    "minimum_focus_distance": [
        r"(?:minimum focus(?:ing)? distance|最近对焦距离)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*(?:m|cm|米|厘米))",
    ],
    "filter_thread": [
        r"(?:filter (?:thread|diameter|size)|滤镜(?:口径|尺寸))\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*mm)",
        r"(?:ø|φ)\s*([0-9]+(?:\.[0-9]+)?\s*mm)",
    ],
}

NEGATIVE_PATTERNS = [
    r"紫边|色散|chromatic aberration|purple fringing|loCA",
    r"卡顿|阻尼|damping|sticky|focus ring",
    r"跑焦|对焦.*慢|autofocus.*slow|hunt(?:ing)?",
    r"眩光|鬼影|flare|ghosting",
    r"重|压手|front-heavy|heavy",
    r"品控|copy variation|decenter",
]

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
        "Zeiss",
        "Sony",
        "Sigma",
        "Canon",
        "Nikon",
        "Fujifilm",
        "Panasonic",
        "Tamron",
        "Leica",
        "DJI",
        "Apple",
        "Samsung",
        "Xiaomi",
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


def extract_specs_from_text(text: str, source_url: str) -> list[OfficialSpec]:
    specs: list[OfficialSpec] = []
    for name, patterns in SPEC_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                specs.append(OfficialSpec(name=name, value=match.group(1).strip(), unit="", source_url=source_url))
                break
    return specs


def infer_specs_from_sku(candidate: ProductCandidate) -> list[OfficialSpec]:
    specs: list[OfficialSpec] = []
    focal = re.search(r"\b([0-9]{2,3})\s*mm\b", candidate.sku, re.I)
    aperture = re.search(r"\bf/?\s*([0-9]+(?:\.[0-9]+)?)\b", candidate.sku, re.I)
    if focal:
        specs.append(OfficialSpec("focal_length", f"{focal.group(1)}mm", "", candidate.source_url))
    if aperture:
        specs.append(OfficialSpec("max_aperture", f"f/{aperture.group(1)}", "", candidate.source_url))
    return specs


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
        evidence.append(
            build_evidence(
                platform=platform,
                url=url,
                author=domain_author(url),
                locator=f"text-match-{index + 1}",
                excerpt=text[start:end],
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
