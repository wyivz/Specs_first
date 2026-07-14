from __future__ import annotations

import unittest

from collectors.mock import MockCollector
from collectors.sources.official import (
    _discovery_conflicts_with_query,
    _discovery_matches_query,
)
from collectors.http import SearchResult


class MockCollectorDiscoverTest(unittest.TestCase):
    def test_discover_uses_query_not_hardcoded_lenses(self) -> None:
        collector = MockCollector()
        candidates = collector.discover_candidates("罗技 G304 无线鼠标", "Product")
        skus = [item.sku for item in candidates]
        self.assertEqual(len(skus), 3)
        self.assertTrue(all("罗技" in sku or "G304" in sku or "无线鼠标" in sku for sku in skus))
        self.assertFalse(any("Zeiss" in sku for sku in skus))
        self.assertFalse(any("Sigma 50mm" in sku for sku in skus))

    def test_mock_specs_follow_category_profile_slots(self) -> None:
        from schemas.category_profile import DynamicCategoryProfile

        collector = MockCollector()
        collector.set_category_profile(
            DynamicCategoryProfile(
                category_label="无线鼠标",
                slots=["connectivity_type", "dpi_range", "battery_life_estimate"],
                source="openai_jit",
            )
        )
        candidate = collector.discover_candidates("罗技 G304", "Product")[0]
        specs, _ = collector.collect_official_specs(candidate)
        names = {spec.name for spec in specs}
        self.assertIn("connectivity_type", names)
        self.assertIn("dpi_range", names)


class OfficialDiscoveryHelperTest(unittest.TestCase):
    def test_discovery_conflicts_mouse_vs_lens(self) -> None:
        self.assertTrue(
            _discovery_conflicts_with_query(
                "罗技 G304 无线鼠标",
                "Sony FE 50mm F1.2 GM",
                "full-frame lens review",
            )
        )

    def test_discovery_matches_mouse_query(self) -> None:
        self.assertTrue(
            _discovery_matches_query(
                "罗技 G304 无线鼠标",
                "Logitech G304 LIGHTSPEED Wireless Gaming Mouse",
                "罗技 G304 无线游戏鼠标评测",
            )
        )


if __name__ == "__main__":
    unittest.main()
