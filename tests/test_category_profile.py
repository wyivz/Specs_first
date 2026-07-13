from __future__ import annotations

import unittest

from collectors.extractors import extract_specs_from_text
from collectors.http import SearchResult
from schemas.category_profile import (
    DynamicCategoryProfile,
    canonical_slots,
    generic_category_profile,
    infer_category,
    normalize_spec_name,
    rank_search_results_for_reviews,
    resolve_category_key,
    slugify_spec_name,
    video_search_queries,
)
from schemas.matrix import build_comparison_matrix
from schemas.models import OfficialSpec, ProductAsset


class CategoryProfileTest(unittest.TestCase):
    def test_slugify_spec_name(self) -> None:
        self.assertEqual(slugify_spec_name("Parameter A"), "parameter_a")
        self.assertEqual(slugify_spec_name("电池容量"), "电池容量")

    def test_video_search_queries_are_category_agnostic(self) -> None:
        queries = dict(video_search_queries("Test SKU"))
        self.assertIn("Bilibili", queries)
        self.assertIn("YouTube", queries)
        self.assertNotIn("紫边", queries["Bilibili"])
        self.assertNotIn("chromatic", queries["YouTube"].lower())

    def test_video_search_queries_append_modifiers(self) -> None:
        queries = dict(video_search_queries("Test SKU", modifiers=["轴体", "热插拔"]))
        self.assertIn("轴体", queries["Bilibili"])
        self.assertIn("热插拔", queries["YouTube"])

    def test_rank_search_results_prefers_review_and_defect_titles(self) -> None:
        results = [
            SearchResult("官方宣传片 开箱仪式", "https://www.bilibili.com/video/BVa", "品牌活动"),
            SearchResult("深度评测：缺点与翻车点汇总", "https://www.bilibili.com/video/BVb", "劝退理由"),
            SearchResult("开箱上手体验", "https://www.bilibili.com/video/BVc", "外观介绍"),
        ]
        ranked = rank_search_results_for_reviews(results)
        self.assertEqual(ranked[0].url, "https://www.bilibili.com/video/BVb")
        self.assertIn("评测", ranked[0].title)

    def test_extract_specs_from_generic_labels(self) -> None:
        text = "参数A: 50mm\n参数B: f/2\n重量: 530g\n参数C: 6 groups / 8 elements"
        specs = extract_specs_from_text(text, "https://example.com/specs")
        names = {spec.name for spec in specs}
        self.assertIn("参数a", names)
        self.assertIn("重量", names)

    def test_infer_category_no_preset_matching(self) -> None:
        self.assertEqual(infer_category("iPhone 15 Pro Max", "Product"), "通用商品")
        self.assertEqual(infer_category("无线机械键盘", "键盘"), "键盘")
        self.assertEqual(resolve_category_key("通用商品"), "generic")
        self.assertEqual(resolve_category_key("咖啡机"), "咖啡机")

    def test_canonical_slots_prefer_profile(self) -> None:
        profile = DynamicCategoryProfile(
            category_label="咖啡机",
            slots=["boiler_pressure", "tank_capacity", "power", "weight", "dimensions"],
            aliases={"压力": "boiler_pressure", "水箱": "tank_capacity"},
            source="openai_jit",
        )
        self.assertEqual(canonical_slots(profile=profile)[0], "boiler_pressure")
        self.assertEqual(canonical_slots("anything")[0], "parameter_a")
        self.assertEqual(len(generic_category_profile().slots), 8)

    def test_normalize_spec_name_uses_dynamic_aliases(self) -> None:
        profile = DynamicCategoryProfile(
            category_label="镜头",
            slots=["focal_length", "max_aperture", "weight", "mount", "filter_diameter"],
            aliases={"focal length": "focal_length", "焦距": "focal_length", "重量": "weight"},
            source="openai_jit",
        )
        self.assertEqual(normalize_spec_name("Focal Length", profile=profile), "focal_length")
        self.assertEqual(normalize_spec_name("焦距", profile=profile), "focal_length")
        self.assertEqual(normalize_spec_name("Some Unique Trait", profile=profile), "some_unique_trait")

    def test_matrix_aligns_to_profile_slots(self) -> None:
        profile = DynamicCategoryProfile(
            category_label="键盘",
            slots=["switch_type", "layout", "weight", "battery_life", "connectivity"],
            source="openai_jit",
        )
        asset = ProductAsset(
            sku="KB-1",
            brand="Test",
            category="键盘",
            official_specs=[
                OfficialSpec("switch_type", "红轴", "", "https://example.com"),
                OfficialSpec("extra_feature", "RGB", "", "https://example.com"),
            ],
            spec_highlights=["extra_feature: RGB"],
            real_world_findings=[],
            prices=[],
            conflict_warnings=[],
            arbitration_summary="",
        )
        matrix = build_comparison_matrix([asset], profile=profile)
        names = [col.key for col in matrix.columns]
        self.assertIn("switch_type", names)
        self.assertIn("layout", names)
        self.assertNotIn("extra_feature", names)
        self.assertEqual(matrix.rows[0]["switch_type"].value, "红轴")
        self.assertEqual(matrix.rows[0]["layout"].value, "")

    def test_profile_roundtrip(self) -> None:
        raw = {
            "category_label": "耳机",
            "slots": ["driver_size", "anc", "battery_life", "weight", "impedance"],
            "aliases": [{"alias": "降噪", "slot": "anc"}, {"alias": "续航", "slot": "battery_life"}],
            "comparison_keywords": ["降噪", "续航"],
            "search_modifiers": ["ANC", "翻车"],
            "source": "openai_jit",
        }
        profile = DynamicCategoryProfile.from_dict(raw)
        self.assertEqual(profile.aliases["降噪"], "anc")
        self.assertEqual(profile.source, "openai_jit")
        again = DynamicCategoryProfile.from_dict(profile.to_dict())
        self.assertEqual(again.slots, profile.slots)


if __name__ == "__main__":
    unittest.main()
