from __future__ import annotations

from datetime import UTC, datetime

from collectors.base import Collector
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate


CAPTURED_AT = datetime(2026, 7, 7, 12, 0, tzinfo=UTC).isoformat()


class MockCollector(Collector):
    def discover_candidates(self, query: str, category: str) -> list[ProductCandidate]:
        return [
            ProductCandidate(
                sku="Zeiss Makro-Planar T* 50mm f/2",
                brand="Zeiss",
                category=category or "Lens",
                source_url="https://www.zeiss.com/mock/makro-planar-50-f2",
                confidence=0.95,
            ),
            ProductCandidate(
                sku="Sony FE 50mm F1.2 GM",
                brand="Sony",
                category=category or "Lens",
                source_url="https://www.sony.com/mock/fe-50mm-f12-gm",
                confidence=0.91,
            ),
            ProductCandidate(
                sku="Sigma 50mm F1.4 DG DN Art",
                brand="Sigma",
                category=category or "Lens",
                source_url="https://www.sigma-global.com/mock/50mm-f14-dg-dn-art",
                confidence=0.89,
            ),
        ]

    def collect_official_specs(self, candidate: ProductCandidate) -> tuple[list[OfficialSpec], list[str]]:
        by_brand = {
            "Zeiss": {
                "focal_length": "50mm",
                "max_aperture": "f/2",
                "weight": "530g",
                "optical_structure": "6 groups / 8 elements",
                "minimum_focus_distance": "0.24m",
                "filter_thread": "67mm",
            },
            "Sony": {
                "focal_length": "50mm",
                "max_aperture": "f/1.2",
                "weight": "778g",
                "optical_structure": "10 groups / 14 elements",
                "minimum_focus_distance": "0.4m",
                "filter_thread": "72mm",
            },
            "Sigma": {
                "focal_length": "50mm",
                "max_aperture": "f/1.4",
                "weight": "670g",
                "optical_structure": "11 groups / 14 elements",
                "minimum_focus_distance": "0.45m",
                "filter_thread": "72mm",
            },
        }
        spec_values = by_brand[candidate.brand]
        specs = [
            OfficialSpec(name=name, value=value, unit="", source_url=candidate.source_url)
            for name, value in spec_values.items()
        ]
        highlights = {
            "Zeiss": ["floating elements", "T* coating"],
            "Sony": ["extreme aspherical elements", "linear XD motors"],
            "Sigma": ["HLA motor", "Art series optical formula"],
        }
        return specs, highlights[candidate.brand]

    def collect_real_world_corpus(self, candidate: ProductCandidate) -> list[EvidenceItem]:
        if candidate.brand == "Zeiss":
            return [
                EvidenceItem(
                    platform="Bilibili",
                    url="https://www.bilibili.com/video/AV12345",
                    author="UP_AV12345",
                    locator="hot-comment-42",
                    captured_at=CAPTURED_AT,
                    excerpt="Wide open, the frame edge has obvious purple fringing in backlit scenes.",
                    confidence=0.88,
                ),
                EvidenceItem(
                    platform="Chiphell",
                    url="https://www.chiphell.com/thread-8876-1-1.html#887",
                    author="chiphell_floor_887",
                    locator="floor-887",
                    captured_at=CAPTURED_AT,
                    excerpt="My copy has uneven focus ring damping, with a slight sticky spot near close focus.",
                    confidence=0.84,
                ),
            ]
        if candidate.brand == "Sony":
            return [
                EvidenceItem(
                    platform="Reddit",
                    url="https://www.reddit.com/r/SonyAlpha/comments/mock50gm/",
                    author="field_user_50gm",
                    locator="comment-c12",
                    captured_at=CAPTURED_AT,
                    excerpt="Autofocus is excellent, but the lens is front-heavy on smaller bodies.",
                    confidence=0.79,
                )
            ]
        return [
            EvidenceItem(
                platform="YouTube",
                url="https://www.youtube.com/watch?v=mock-sigma-50-art",
                author="reviewer_sigma_art",
                locator="caption-00:12:18",
                captured_at=CAPTURED_AT,
                excerpt="Strong center sharpness, but longitudinal CA is visible before stopping down.",
                confidence=0.81,
            )
        ]

    def collect_prices(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        prices = {
            "Zeiss": (5999, 500, 600, 0, 4899, "vault_output/mock_screenshots/zeiss_price.png"),
            "Sony": (14999, 1200, 1800, 300, 11699, "vault_output/mock_screenshots/sony_price.png"),
            "Sigma": (6499, 400, 700, 0, 5399, "vault_output/mock_screenshots/sigma_price.png"),
        }
        list_price, coupon, subsidy, cross_store, final, screenshot = prices[candidate.brand]
        evidence = EvidenceItem(
            platform="JD",
            url=f"https://item.jd.com/mock-{candidate.brand.lower()}-50mm.html",
            author="JD product page",
            locator="price-panel-screenshot",
            captured_at=CAPTURED_AT,
            excerpt=f"List {list_price}, coupon {coupon}, subsidy {subsidy}, cross-store {cross_store}, final {final}.",
            confidence=0.86,
        )
        return [
            PriceFinding(
                platform="JD",
                list_price=list_price,
                coupon_discount=coupon,
                subsidy_discount=subsidy,
                cross_store_discount=cross_store,
                final_price=final,
                screenshot_path=screenshot,
                captured_at=CAPTURED_AT,
                evidence=evidence,
            )
        ]
