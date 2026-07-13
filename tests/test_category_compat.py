from __future__ import annotations

import unittest

from collectors.extractors import extract_specs_from_text
from schemas.category_profile import (
    DynamicCategoryProfile,
    infer_category,
    normalize_spec_name,
    resolve_category_key,
)


class CategoryCompatibilityTest(unittest.TestCase):
    def test_infer_category_keeps_explicit_hint_only(self) -> None:
        self.assertEqual(infer_category("iPhone 15 Pro Max", "Product"), "通用商品")
        self.assertEqual(infer_category("无线机械键盘 75%", "Product"), "通用商品")
        self.assertEqual(infer_category("Sony WH-1000XM5", "耳机"), "耳机")
        self.assertEqual(infer_category("SEL50F12GM", "Lens"), "Lens")
        self.assertEqual(resolve_category_key(infer_category("MacBook Air M3", "")), "generic")

    def test_dynamic_profile_normalizes_phone_like_labels(self) -> None:
        profile = DynamicCategoryProfile(
            category_label="手机",
            slots=[
                "chipset",
                "ram",
                "battery_capacity",
                "weight",
                "screen_size",
                "storage",
                "main_camera",
                "resolution",
            ],
            aliases={
                "处理器": "chipset",
                "运行内存": "ram",
                "电池": "battery_capacity",
                "重量": "weight",
            },
            source="openai_jit",
        )
        text = "处理器: A17 Pro\n运行内存: 8GB\n电池容量: 4422mAh\n重量: 221g\nf/1.78 somewhere"
        specs = {
            s.name: s.value
            for s in extract_specs_from_text(text, "https://example.com/phone", "手机", profile=profile)
        }
        self.assertEqual(specs.get("chipset"), "A17 Pro")
        self.assertEqual(specs.get("ram"), "8GB")
        self.assertEqual(specs.get("battery_capacity"), "4422 mAh")
        self.assertEqual(specs.get("weight"), "221 g")
        self.assertEqual(normalize_spec_name("运行内存", profile=profile), "ram")

    def test_no_preset_template_keys(self) -> None:
        # Preset match_keywords are gone; free-form labels slugify instead.
        self.assertEqual(resolve_category_key("机械键盘"), "机械键盘")
        self.assertEqual(resolve_category_key("降噪耳机"), "降噪耳机")
        self.assertEqual(resolve_category_key("Product"), "generic")


if __name__ == "__main__":
    unittest.main()
