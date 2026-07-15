from __future__ import annotations

import unittest
from typing import Any

from backend.discover_normalize import discover_skus_from_evidence, merge_discovery_candidates, usable_discovered_sku
from collectors.http import SearchResult
from collectors.mock import MockCollector
from collectors.real import RealCollector
from collectors.sources.official import OfficialSourceCollector
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


class AiDiscoveryNormalizeTest(unittest.TestCase):
    def test_llm_maps_listicle_hits_to_models_any_category(self) -> None:
        hits = [
            SearchResult(
                title="2026年最建议买的10个蓝牙键盘品牌推荐",
                url="https://zhuanlan.zhihu.com/p/1",
                snippet="文中对比了罗技 K380、罗技 MX Keys、Apple Magic Keyboard",
            ),
            SearchResult(
                title="你绝对想不到蓝牙键盘可以这样折叠",
                url="https://www.ifanr.com/556817",
                snippet="折叠键盘体验文章，没有具体型号",
            ),
        ]

        def fake_llm(_system: str, prompt: str) -> dict[str, Any]:
            self.assertIn("body=", prompt)
            return {
                "products": [
                    {"sku": "Logitech K380", "brand": "Logitech", "evidence_index": 1},
                    {"sku": "Logitech MX Keys", "brand": "Logitech", "evidence_index": 1},
                    {"sku": "Apple Magic Keyboard", "brand": "Apple", "evidence_index": 1},
                    {
                        "sku": "你绝对想不到蓝牙键盘可以这样折叠",
                        "brand": "Unknown",
                        "evidence_index": 2,
                    },
                ]
            }

        def fake_fetch(url: str) -> str:
            if "zhihu" in url:
                return "<html>推荐购买 Logitech K380、MX Keys 与 Apple Magic Keyboard</html>"
            return "<html>无具体型号</html>"

        candidates = discover_skus_from_evidence(
            "蓝牙键盘",
            hits,
            category="Product",
            llm_json=fake_llm,
            page_fetcher=fake_fetch,
        )
        skus = [item.sku for item in candidates]
        self.assertIn("Logitech K380", skus)
        self.assertIn("Logitech MX Keys", skus)
        self.assertIn("Apple Magic Keyboard", skus)
        self.assertNotIn("你绝对想不到蓝牙键盘可以这样折叠", skus)
        self.assertEqual(candidates[0].source_url, hits[0].url)

    def test_rejects_copied_search_titles_even_if_llm_returns_them(self) -> None:
        hits = [
            SearchResult("十款值得买的真皮篮球产品榜", "https://example.com/a", "榜单"),
        ]

        def fake_llm(_system: str, _prompt: str) -> dict[str, Any]:
            return {
                "products": [
                    {"sku": "十款值得买的真皮篮球产品榜", "brand": "Unknown", "evidence_index": 1},
                    {"sku": "Spalding TF-1000", "brand": "Spalding", "evidence_index": 1},
                ]
            }

        candidates = discover_skus_from_evidence(
            "篮球",
            hits,
            llm_json=fake_llm,
            fetch_bodies=False,
        )
        skus = [item.sku for item in candidates]
        self.assertEqual(skus, ["Spalding TF-1000"])

    def test_real_collector_uses_ai_normalizer_not_title_as_sku(self) -> None:
        class _Http:
            def search(self, query, max_results=8, *, quick=False):
                return [
                    SearchResult(
                        title="【全民众测】五款无线游戏鼠标，谁才是卷王？",
                        url="https://post.smzdm.com/p/a7nm8w5g/",
                        snippet="对比罗技 G304、雷蛇 Viper V3 Pro、雷柏 VT9 Pro",
                    )
                ]

        def fake_llm(_system: str, _prompt: str) -> dict[str, Any]:
            return {
                "products": [
                    {"sku": "Logitech G304", "brand": "Logitech", "evidence_index": 1},
                    {"sku": "Razer Viper V3 Pro", "brand": "Razer", "evidence_index": 1},
                    {"sku": "Rapoo VT9 Pro", "brand": "Rapoo", "evidence_index": 1},
                ]
            }

        collector = RealCollector(http=_Http(), browser=object())  # type: ignore[arg-type]
        # Avoid Playwright launch in unit test.
        collector.browser = type("B", (), {})()
        candidates = collector.discover_candidates(
            "无线游戏鼠标",
            "Product",
            quick=True,
            llm_json=fake_llm,
        )
        skus = [item.sku for item in candidates]
        self.assertEqual(
            skus,
            ["Logitech G304", "Razer Viper V3 Pro", "Rapoo VT9 Pro"],
        )
        self.assertFalse(any("全民众测" in sku or "五款" in sku for sku in skus))

    def test_official_collects_hits_without_inventing_skus(self) -> None:
        class _Http:
            def search(self, query, max_results=8, *, quick=False):
                return [
                    SearchResult("headline only", "https://example.com/a", "snippet"),
                ]

        collector = OfficialSourceCollector(_Http())  # type: ignore[arg-type]
        hits = collector.collect_discovery_hits("任意品类词", "Product", quick=True)
        self.assertEqual(len(hits), 1)
        self.assertEqual(collector.discover_candidates("任意品类词", "Product", quick=True), [])

    def test_merge_dedupes_by_identity(self) -> None:
        primary = [
            ProductCandidate("Logitech G304", "Logitech", "Product", "https://a", 0.8),
        ]
        secondary = [
            ProductCandidate("logitech g304", "Logitech", "Product", "https://b", 0.7),
            ProductCandidate("Razer Viper V3 Pro", "Razer", "Product", "https://c", 0.75),
        ]
        merged = merge_discovery_candidates(primary, secondary, max_results=10)
        skus = [item.sku for item in merged]
        self.assertEqual(skus, ["Logitech G304", "Razer Viper V3 Pro"])

    def test_usable_sku_is_structural_only(self) -> None:
        self.assertTrue(usable_discovered_sku("Some Brand Model X1"))
        self.assertFalse(usable_discovered_sku(""))
        self.assertFalse(usable_discovered_sku("x" * 100))


if __name__ == "__main__":
    unittest.main()
