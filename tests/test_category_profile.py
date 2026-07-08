from __future__ import annotations

import unittest

from collectors.extractors import extract_specs_from_text
from schemas.category_profile import (
    canonical_slots,
    normalize_spec_name,
    resolve_category_key,
    slugify_spec_name,
    video_search_queries,
)


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

    def test_extract_specs_from_generic_labels(self) -> None:
        text = "参数A: 50mm\n参数B: f/2\n重量: 530g\n参数C: 6 groups / 8 elements"
        specs = extract_specs_from_text(text, "https://example.com/specs")
        names = {spec.name for spec in specs}
        self.assertIn("参数a", names)
        self.assertIn("重量", names)

    def test_resolve_category_key_matches_known_categories(self) -> None:
        self.assertEqual(resolve_category_key("Lens"), "lens")
        self.assertEqual(resolve_category_key("镜头"), "lens")
        self.assertEqual(resolve_category_key("Smartphone"), "phone")
        self.assertEqual(resolve_category_key("some unknown gadget"), "generic")

    def test_canonical_slots_are_category_specific(self) -> None:
        self.assertIn("focal_length", canonical_slots("Lens"))
        self.assertIn("battery_capacity", canonical_slots("Phone"))
        self.assertEqual(canonical_slots("unmodeled category")[0], "parameter_a")

    def test_normalize_spec_name_collapses_bilingual_aliases(self) -> None:
        self.assertEqual(normalize_spec_name("Focal Length", "Lens"), "focal_length")
        self.assertEqual(normalize_spec_name("焦距", "镜头"), "focal_length")
        self.assertEqual(normalize_spec_name("Some Unique Trait", "Lens"), "some_unique_trait")

    def test_extract_specs_normalizes_bilingual_labels_for_known_category(self) -> None:
        text_en = "Focal Length: 50mm\nAperture: f/1.4\nWeight: 645g"
        text_zh = "焦距: 50mm\n光圈: f/1.4\n重量: 645g"
        specs_en = {spec.name: spec.value for spec in extract_specs_from_text(text_en, "https://example.com/en", "Lens")}
        specs_zh = {spec.name: spec.value for spec in extract_specs_from_text(text_zh, "https://example.com/zh", "镜头")}
        self.assertEqual(set(specs_en.keys()), set(specs_zh.keys()))
        self.assertIn("focal_length", specs_en)
        self.assertIn("max_aperture", specs_en)
        self.assertIn("weight", specs_en)


if __name__ == "__main__":
    unittest.main()
