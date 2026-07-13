from __future__ import annotations

import unittest

from collectors.extractors import evidence_mentions_sku, extract_specs_from_text
from collectors.url_guards import is_noisy_ecommerce_url


class EvidenceRelevanceAndSpecQualityTest(unittest.TestCase):
    def test_sku_mention_matches_model_code(self) -> None:
        self.assertTrue(
            evidence_mentions_sku(
                "SEL50F12GM",
                "索尼 FE 50mm F1.2 GM 评测 SEL50F12GM 品控",
            )
        )
        self.assertFalse(
            evidence_mentions_sku(
                "SEL50F12GM",
                "Asus TUF laptop overheating thermal issues with Wi-Fi card",
            )
        )

    def test_jd_homepage_is_noisy(self) -> None:
        self.assertTrue(is_noisy_ecommerce_url("https://www.jd.com/?from=pc_item_sd"))

    def test_skips_jd_footer_labels(self) -> None:
        text = (
            "违法和不良信息举报电话: 4006561155\n"
            "消费者维权热线: 4006067733\n"
            "Focal Length: 50 mm\n"
            "Aperture: f/1.2\n"
            "Weight: 778 g\n"
        )
        specs = {s.name: s.value for s in extract_specs_from_text(text, "https://www.sony.com/x", "Lens")}
        self.assertNotIn("违法和不良信息举报电话", specs)
        self.assertNotIn("消费者维权热线", specs)
        self.assertEqual(specs.get("focal_length"), "50 mm")
        self.assertEqual(specs.get("max_aperture"), "f/1.2")
        self.assertEqual(specs.get("weight"), "778 g")

    def test_measurement_backfill_does_not_swap_lens_slots(self) -> None:
        # Only focal length labeled; bare measurements must not fill aperture with "0.4 m".
        text = "Focal Length: 50 mm\nMinimum focus distance is 0.4 m and the lens weighs 778 g. Filter 72 mm."
        specs = {s.name: s.value for s in extract_specs_from_text(text, "https://example.com", "Lens")}
        self.assertEqual(specs.get("focal_length"), "50 mm")
        self.assertNotEqual(specs.get("max_aperture"), "0.4 m")
        self.assertEqual(specs.get("weight"), "778 g")
        self.assertEqual(specs.get("min_focus_distance"), "0.4 m")

    def test_sony_style_adjacent_labels(self) -> None:
        text = (
            "Minimum Focus Distance 1.32 ft (0.4 m) Focal Length (mm) 50 "
            "Maximum aperture (F) 1.2 Filter Diameter (mm) 72 "
            "Weight 27.5 oz (778 g) Mount Sony E-mount "
            "35 mm equivalent focal length (APS-C) 75"
        )
        specs = {s.name: s.value for s in extract_specs_from_text(text, "https://www.sony.com/x", "Lens")}
        self.assertEqual(specs.get("focal_length"), "50 mm")
        self.assertEqual(specs.get("max_aperture"), "f/1.2")
        self.assertEqual(specs.get("filter_diameter"), "72 mm")
        self.assertEqual(specs.get("weight"), "778 g")
        self.assertEqual(specs.get("min_focus_distance"), "0.4 m")
        self.assertEqual(specs.get("mount"), "Sony E-mount")
        # Minimum aperture must not overwrite max aperture.
        text2 = text + " Minimum Aperture (F) 16"
        specs2 = {s.name: s.value for s in extract_specs_from_text(text2, "https://www.sony.com/x", "Lens")}
        self.assertEqual(specs2.get("max_aperture"), "f/1.2")


if __name__ == "__main__":
    unittest.main()
