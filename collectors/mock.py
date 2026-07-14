from __future__ import annotations

import re
from datetime import UTC, datetime

from collectors.base import Collector
from collectors.extractors import infer_brand
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate
from schemas.category_profile import DynamicCategoryProfile, canonical_slots


CAPTURED_AT = datetime(2026, 7, 7, 12, 0, tzinfo=UTC).isoformat()

_DISCOVER_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "official",
    "specifications",
    "product",
    "品类",
    "通用",
    "对比",
    "评测",
}


class MockCollector(Collector):
    """Query-driven mock collector for offline demos — not tied to any product category."""

    category_profile: DynamicCategoryProfile | None = None

    def set_category_profile(self, profile: DynamicCategoryProfile | None) -> None:
        self.category_profile = profile

    def discover_candidates(self, query: str, category: str) -> list[ProductCandidate]:
        seed = (query or "").strip() or (category or "").strip() or "Demo Product"
        brand = infer_brand(seed)
        labels = _mock_compare_labels(seed)
        resolved_category = (category or "Product").strip() or "Product"
        return [
            ProductCandidate(
                sku=label,
                brand=brand,
                category=resolved_category,
                source_url=f"https://example.invalid/mock/{index}",
                confidence=0.95 - index * 0.03,
            )
            for index, label in enumerate(labels)
        ]

    def collect_official_specs(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        del task_id, use_browser, storage_state_path
        slots = list(canonical_slots(candidate.category, profile=self.category_profile))
        demo_values = _demo_values_for_slots(slots, candidate.sku)
        specs = [
            OfficialSpec(
                name=slot,
                value=demo_values.get(slot, f"demo-{slot}"),
                unit="",
                source_url=candidate.source_url,
            )
            for slot in slots[:8]
        ]
        highlights = [f"Mock highlight for {candidate.sku}"]
        return specs, highlights

    def collect_real_world_corpus(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[EvidenceItem]:
        del task_id, use_browser, storage_state_path
        sku = candidate.sku
        return [
            EvidenceItem(
                platform="Forum",
                url=f"https://example.invalid/mock/{sku}/forum",
                author="mock_user",
                locator="post-1",
                captured_at=CAPTURED_AT,
                excerpt=(
                    f"{sku}: occasional quality-control variation reported after extended daily use; "
                    "not a universal defect but worth checking recent batches."
                ),
                confidence=0.84,
            ),
            EvidenceItem(
                platform="Video",
                url=f"https://example.invalid/mock/{sku}/review",
                author="mock_reviewer",
                locator="caption-03:12",
                captured_at=CAPTURED_AT,
                excerpt=(
                    f"{sku}: performance is solid for the price, though ergonomics and battery "
                    "endurance may disappoint power users."
                ),
                confidence=0.81,
            ),
        ]

    def collect_prices(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        del task_id, use_browser, storage_state_path
        index = abs(hash(candidate.sku)) % 3
        list_price = 399.0 + index * 120
        coupon = 30.0 + index * 10
        subsidy = 20.0
        final = list_price - coupon - subsidy
        evidence = EvidenceItem(
            platform="JD",
            url=f"https://example.invalid/mock/{candidate.sku}/price",
            author="mock marketplace",
            locator="price-panel",
            captured_at=CAPTURED_AT,
            excerpt=f"List {list_price}, coupon {coupon}, subsidy {subsidy}, final {final}.",
            confidence=0.86,
        )
        return [
            PriceFinding(
                platform="JD",
                list_price=list_price,
                coupon_discount=coupon,
                subsidy_discount=subsidy,
                cross_store_discount=0.0,
                final_price=final,
                screenshot_path="",
                captured_at=CAPTURED_AT,
                evidence=evidence,
            )
        ]


def _mock_compare_labels(seed: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", seed.strip())
    if not cleaned:
        return ["Demo Product A", "Demo Product B", "Demo Product C"]
    tokens = [
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]+", cleaned)
        if len(token) >= 2 and token.lower() not in _DISCOVER_STOP_WORDS
    ]
    core = cleaned[:120]
    if len(tokens) >= 2:
        short = " ".join(tokens[:3])[:120]
        alt_a = f"{short} Pro"[:120]
        alt_b = f"{short} Lite"[:120]
        labels = [core, alt_a, alt_b]
    else:
        labels = [core, f"{core} (Alt A)"[:120], f"{core} (Alt B)"[:120]]
    unique: list[str] = []
    seen: set[str] = set()
    for label in labels:
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(label)
    while len(unique) < 3:
        unique.append(f"{core} Option {len(unique) + 1}"[:120])
    return unique[:3]


def _demo_values_for_slots(slots: list[str], sku: str) -> dict[str, str]:
    seed = abs(hash(sku))
    presets: dict[str, list[str]] = {
        "connectivity_type": ["2.4 GHz + Bluetooth", "Bluetooth 5.3", "USB-C wired"],
        "dpi_range": ["200–12000", "100–25600", "400–16000"],
        "battery_life_estimate": ["70 h", "95 h", "120 h"],
        "sensor_performance": ["HERO-class", "PAW3395", "Focus Pro 30K"],
        "weight": ["63 g", "78 g", "92 g"],
        "switch_type": ["Optical", "Mechanical tactile", "Hall-effect"],
        "layout": ["75%", "TKL", "Full-size"],
        "keycap_material": ["PBT double-shot", "ABS", "PBT dye-sub"],
        "focal_length": ["50mm", "35mm", "85mm"],
        "max_aperture": ["f/1.2", "f/1.4", "f/2"],
        "mount": ["Sony E-mount", "Canon RF", "Nikon Z"],
    }
    values: dict[str, str] = {}
    for index, slot in enumerate(slots):
        options = presets.get(slot)
        if options:
            values[slot] = options[(seed + index) % len(options)]
        elif slot.startswith("parameter_"):
            values[slot] = f"demo-{slot}-{index + 1}"
        else:
            values[slot] = f"demo-{slot}"
    return values
