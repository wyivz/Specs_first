from __future__ import annotations

import unittest

from collectors.extractors import (
    extract_product_skus_from_hit,
    is_category_or_list_url,
    is_concrete_product_sku,
    is_listicle_title,
)
from collectors.http import SearchResult
from collectors.mock import MockCollector
from collectors.sources.official import (
    OfficialSourceCollector,
    _discovery_conflicts_with_query,
    _discovery_matches_query,
)
from backend.discover_normalize import merge_discovery_candidates
from schemas import ProductCandidate


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


class ProductSkuExtractionTest(unittest.TestCase):
    def test_listicle_title_detected(self) -> None:
        self.assertTrue(is_listicle_title("【全民众测】五款无线游戏鼠标，谁才是卷王？"))
        self.assertTrue(is_listicle_title("无线游戏鼠标怎么选？2026这8款低延迟推荐"))
        self.assertFalse(is_listicle_title("罗技 G Pro 无线游戏鼠标"))

    def test_category_url_detected(self) -> None:
        self.assertTrue(is_category_or_list_url("https://www.logitechg.com/zh-cn/shop/c/gaming-mice"))
        self.assertFalse(is_category_or_list_url("https://www.logitechg.com/zh-cn/shop/p/pro-wireless-mouse"))

    def test_extract_models_from_roundup_title(self) -> None:
        pairs = extract_product_skus_from_hit(
            "无线游戏鼠标怎么选？2026这8款低延迟电竞鼠标推荐，别乱买了|罗技|雷柏|雷蛇|无线鼠标",
            "对比了罗技 G304、雷蛇 Viper V3 Pro 与雷柏 VT9 Pro",
        )
        skus = " ".join(sku for sku, _ in pairs).lower()
        self.assertTrue(any("g304" in sku.lower() for sku, _ in pairs) or "g304" in skus)
        self.assertTrue(any("viper" in sku.lower() for sku, _ in pairs))
        self.assertTrue(any("vt" in sku.lower() and "9" in sku for sku, _ in pairs))
        self.assertFalse(any(is_listicle_title(sku) for sku, _ in pairs))

    def test_concrete_sku_rejects_article_headline(self) -> None:
        self.assertFalse(is_concrete_product_sku("【全民众测】五款无线游戏鼠标，谁才是卷王？"))
        self.assertTrue(is_concrete_product_sku("Logitech G304"))
        self.assertTrue(is_concrete_product_sku("罗技 G Pro Wireless"))


class OfficialDiscoveryCandidateTest(unittest.TestCase):
    def test_listicle_hits_become_concrete_models_not_headlines(self) -> None:
        class _Http:
            def search(self, query, max_results=8):
                return [
                    SearchResult(
                        title="【全民众测】五款无线游戏鼠标，谁才是卷王？",
                        url="https://post.smzdm.com/p/a7nm8w5g/",
                        snippet="对比罗技 G304、雷蛇 Viper V3 Pro、雷柏 VT9 Pro 等热门型号",
                    ),
                    SearchResult(
                        title="罗技 G Pro 无线游戏鼠标（专为电竞设计）",
                        url="https://www.logitechg.com/zh-cn/shop/p/pro-wireless-mouse",
                        snippet="Logitech G Pro Wireless gaming mouse",
                    ),
                    SearchResult(
                        title="游戏鼠标",
                        url="https://www.logitechg.com/zh-cn/shop/c/gaming-mice",
                        snippet="分类页",
                    ),
                ]

        collector = OfficialSourceCollector(_Http())  # type: ignore[arg-type]
        candidates = collector.discover_candidates("无线游戏鼠标", "Product")
        skus = [item.sku for item in candidates]
        self.assertTrue(skus)
        self.assertTrue(all(is_concrete_product_sku(sku) for sku in skus))
        self.assertFalse(any("全民众测" in sku or "五款" in sku for sku in skus))
        joined = " ".join(skus).lower()
        self.assertTrue("g304" in joined or "g pro" in joined or "viper" in joined)

    def test_merge_prefers_concrete_models(self) -> None:
        primary = [
            ProductCandidate("bad headline 五款推荐", "Unknown", "Product", "https://x", 0.5),
            ProductCandidate("Logitech G304", "Logitech", "Product", "https://a", 0.8),
        ]
        secondary = [
            ProductCandidate("Razer Viper V3 Pro", "Razer", "Product", "https://b", 0.75),
        ]
        merged = merge_discovery_candidates(primary, secondary, max_results=10)
        skus = [item.sku for item in merged]
        self.assertEqual(skus, ["Logitech G304", "Razer Viper V3 Pro"])


if __name__ == "__main__":
    unittest.main()
