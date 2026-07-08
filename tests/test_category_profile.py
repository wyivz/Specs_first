from __future__ import annotations

import unittest

from collectors.extractors import extract_specs_from_text
from schemas.category_profile import slugify_spec_name, video_search_queries


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


if __name__ == "__main__":
    unittest.main()
