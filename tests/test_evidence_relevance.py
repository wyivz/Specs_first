from __future__ import annotations

import unittest

from collectors.extractors import (
    evidence_from_page,
    evidence_from_search_result,
    evidence_mentions_sku,
    extract_specs_from_text,
    page_matches_sku,
    sku_search_phrase,
)
from collectors.http import SearchResult
from collectors.url_guards import is_noisy_ecommerce_url
from schemas.category_profile import (
    DynamicCategoryProfile,
    ecommerce_search_queries,
    rank_search_results_for_reviews,
)


def _lens_jit_profile() -> DynamicCategoryProfile:
    """Stand-in for ChatGPT JIT output used by keyword extractor tests."""
    return DynamicCategoryProfile(
        category_label="镜头",
        slots=[
            "focal_length",
            "max_aperture",
            "optical_structure",
            "filter_diameter",
            "weight",
            "mount",
            "min_focus_distance",
            "image_stabilization",
        ],
        aliases={
            "focal length": "focal_length",
            "焦距": "focal_length",
            "maximum aperture": "max_aperture",
            "max aperture": "max_aperture",
            "aperture": "max_aperture",
            "最大光圈": "max_aperture",
            "filter": "filter_diameter",
            "口径": "filter_diameter",
            "weight": "weight",
            "重量": "weight",
            "mount": "mount",
            "卡口": "mount",
            "minimum focus": "min_focus_distance",
            "最近对焦": "min_focus_distance",
        },
        comparison_keywords=["焦距", "光圈", "重量"],
        search_modifiers=["评测", "色散"],
        source="openai_jit",
    )


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

    def test_sku_mention_accepts_marketplace_alias_without_model_code(self) -> None:
        self.assertTrue(
            evidence_mentions_sku(
                "SEL50F12GM",
                "索尼 FE 50mm F1.2 GM 全画幅微单镜头 官方标配",
            )
        )
        self.assertFalse(
            evidence_mentions_sku(
                "SEL50F12GM",
                "索尼 FE 85mm F1.4 GM 镜头开箱",
            )
        )

    def test_sku_mention_rejects_shared_focal_length_only(self) -> None:
        self.assertFalse(
            evidence_mentions_sku(
                "SEL50F12GM",
                "Best 50mm lens for beginners budget review",
            )
        )

    def test_marketing_name_matches_brand_and_focal(self) -> None:
        self.assertTrue(
            evidence_mentions_sku(
                "Zeiss Makro-Planar T* 50mm f/2",
                "JD Zeiss 50mm 到手价",
            )
        )
        self.assertFalse(
            evidence_mentions_sku(
                "Zeiss Makro-Planar T* 50mm f/2",
                "JD Zeiss 85mm Batis",
            )
        )

    def test_search_phrase_quotes_model_codes(self) -> None:
        self.assertEqual(sku_search_phrase("SEL50F12GM"), '"SEL50F12GM"')
        self.assertIn('"SEL50F12GM"', dict(ecommerce_search_queries("SEL50F12GM"))["JD"])

    def test_rank_prefers_sku_match_over_generic_review(self) -> None:
        results = [
            SearchResult("深度评测：随便说说翻车点", "https://www.bilibili.com/video/BVa", "劝退理由"),
            SearchResult("SEL50F12GM 开箱", "https://www.bilibili.com/video/BVb", "外观介绍"),
            SearchResult("SEL50F12GM 评测缺点翻车", "https://www.bilibili.com/video/BVc", "品控问题"),
        ]
        ranked = rank_search_results_for_reviews(results, sku="SEL50F12GM")
        self.assertEqual(ranked[0].url, "https://www.bilibili.com/video/BVc")
        self.assertEqual(ranked[-1].url, "https://www.bilibili.com/video/BVa")

    def test_page_matches_sku_uses_title(self) -> None:
        self.assertTrue(
            page_matches_sku(
                "SEL50F12GM",
                title="索尼 FE 50mm F1.2 GM SEL50F12GM",
                text="价格说明",
            )
        )
        self.assertFalse(
            page_matches_sku(
                "SEL50F12GM",
                title="华硕 TUF Gaming 笔记本",
                text="散热翻车 过热",
            )
        )

    def test_jd_homepage_is_noisy(self) -> None:
        self.assertTrue(is_noisy_ecommerce_url("https://www.jd.com/?from=pc_item_sd"))

    def test_jd_frequency_control_page_is_noisy_and_rate_limited(self) -> None:
        from collectors.browser import PlaywrightCapture
        from collectors.page_sanitize import detect_page_blockers
        from collectors.url_guards import is_rate_limited_ecommerce_url

        freq = "https://pc-frequent-pro.pf.jd.com/?from=pc_item&reason=403"
        self.assertTrue(is_rate_limited_ecommerce_url(freq))
        self.assertTrue(is_noisy_ecommerce_url(freq))
        # Requested product URL + landed on freq control → never open headed captcha.
        self.assertTrue(
            PlaywrightCapture.should_skip_headed_captcha(
                "https://item.jd.com/100010708487.html",
                freq,
            )
        )
        blockers = detect_page_blockers(freq, "<html></html>", "")
        self.assertTrue(any(b.kind == "rate_limited" for b in blockers))
        self.assertFalse(any(b.kind == "auth_or_captcha" for b in blockers))

    def test_evidence_from_page_accepts_defect_without_review_word(self) -> None:
        markup = (
            "<html><body><h1>SEL50F12GM 开箱</h1>"
            "<p>这支镜头偶发色散和紫边，整体画质还不错。</p>"
            "</body></html>"
        )
        items = evidence_from_page("Chiphell", "https://www.chiphell.com/t1", markup, sku="SEL50F12GM")
        self.assertTrue(items)
        self.assertTrue(any("色散" in item.excerpt or "紫边" in item.excerpt for item in items))

    def test_evidence_from_search_accepts_review_wording(self) -> None:
        result = SearchResult(
            "SEL50F12GM 长期体验",
            "https://www.bilibili.com/video/BVx",
            "开箱评测记录日常使用感受",
        )
        item = evidence_from_search_result("Bilibili", result, sku="SEL50F12GM")
        self.assertIsNotNone(item)

    def test_skips_jd_footer_labels(self) -> None:
        text = (
            "违法和不良信息举报电话: 4006561155\n"
            "消费者维权热线: 4006067733\n"
            "Focal Length: 50 mm\n"
            "Aperture: f/1.2\n"
            "Weight: 778 g\n"
        )
        specs = {
            s.name: s.value
            for s in extract_specs_from_text(text, "https://www.sony.com/x", "Lens", profile=_lens_jit_profile())
        }
        self.assertNotIn("违法和不良信息举报电话", specs)
        self.assertNotIn("消费者维权热线", specs)
        self.assertEqual(specs.get("focal_length"), "50 mm")
        self.assertEqual(specs.get("max_aperture"), "f/1.2")
        self.assertEqual(specs.get("weight"), "778 g")

    def test_measurement_backfill_does_not_swap_lens_slots(self) -> None:
        # Only focal length labeled; bare measurements must not fill aperture with "0.4 m".
        text = "Focal Length: 50 mm\nMinimum focus distance is 0.4 m and the lens weighs 778 g. Filter 72 mm."
        specs = {
            s.name: s.value
            for s in extract_specs_from_text(text, "https://example.com", "Lens", profile=_lens_jit_profile())
        }
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
        profile = _lens_jit_profile()
        specs = {
            s.name: s.value
            for s in extract_specs_from_text(text, "https://www.sony.com/x", "Lens", profile=profile)
        }
        self.assertEqual(specs.get("focal_length"), "50 mm")
        self.assertEqual(specs.get("max_aperture"), "f/1.2")
        self.assertEqual(specs.get("filter_diameter"), "72 mm")
        self.assertEqual(specs.get("weight"), "778 g")
        self.assertEqual(specs.get("min_focus_distance"), "0.4 m")
        self.assertEqual(specs.get("mount"), "Sony E-mount")
        # Minimum aperture must not overwrite max aperture.
        text2 = text + " Minimum Aperture (F) 16"
        specs2 = {
            s.name: s.value
            for s in extract_specs_from_text(text2, "https://www.sony.com/x", "Lens", profile=profile)
        }
        self.assertEqual(specs2.get("max_aperture"), "f/1.2")

    def test_rejects_competitor_lens_without_sony_context(self) -> None:
        self.assertFalse(
            evidence_mentions_sku(
                "SEL50F12GM",
                "Canon RF 50mm f/1.2L USM review photography life",
            )
        )

    def test_infer_specs_from_sony_sel_code(self) -> None:
        from collectors.extractors import infer_specs_from_sku
        from schemas import ProductCandidate

        specs = infer_specs_from_sku(
            ProductCandidate(
                sku="SEL50F12GM",
                brand="Sony",
                category="Lens",
                source_url="https://www.sony.com/",
                confidence=0.5,
            )
        )
        names = {spec.name for spec in specs}
        self.assertIn("focal_length", names)
        self.assertIn("max_aperture", names)
        self.assertIn("mount", names)

    def test_clean_evidence_excerpt_strips_forum_chrome(self) -> None:
        from collectors.extractors import clean_evidence_excerpt

        raw = "积分 464159 当前离线 回复 举报 全开1.2边缘对焦不准"
        cleaned = clean_evidence_excerpt(raw)
        self.assertNotIn("积分", cleaned)
        self.assertNotIn("回复", cleaned)
        self.assertIn("边缘对焦", cleaned)

    def test_chiphell_post_extractor_prefers_post_body(self) -> None:
        from collectors.page_sanitize import extract_forum_post_text, sanitize_html

        markup = (
            "<html><body>"
            '<div class="pls">积分 999 当前离线</div>'
            '<td id="postmessage_123" class="t_f">'
            "SEL50F12GM wide open edge AF is unreliable in low light and backlight."
            "</td>"
            "</body></html>"
        )
        focused = extract_forum_post_text(markup)
        self.assertIn("edge AF", focused)
        self.assertNotIn("积分 999", focused)
        page = sanitize_html("https://www.chiphell.com/thread-1-1.html", markup)
        self.assertIn("edge AF", page.text)


if __name__ == "__main__":
    unittest.main()
