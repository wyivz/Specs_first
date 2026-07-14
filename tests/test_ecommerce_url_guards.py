from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from collectors.adapters.jd import JdAdapter
from collectors.adapters.registry import AdapterRegistry
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.browser import PlaywrightCapture
from collectors.http import SearchResult
from collectors.sources.ecommerce import EcommerceSourceCollector
from collectors.url_guards import is_noisy_ecommerce_url
from schemas.category_profile import ecommerce_search_queries


class EcommerceProductUrlGuardTest(unittest.TestCase):
    def test_noisy_url_guard(self) -> None:
        self.assertTrue(is_noisy_ecommerce_url("https://campus.jd.com/"))
        self.assertTrue(is_noisy_ecommerce_url("https://music.jd.com/x?key={keyword}"))
        self.assertTrue(is_noisy_ecommerce_url("https://www.jd.com/?from=pc_item_sd"))
        self.assertTrue(is_noisy_ecommerce_url("https://www.jd.com/brand/abc.html"))
        self.assertFalse(is_noisy_ecommerce_url("https://item.jd.com/100010708487.html"))
        self.assertFalse(is_noisy_ecommerce_url("https://detail.tmall.com/item.htm?id=123"))

    def test_ecommerce_queries_ignore_review_modifiers(self) -> None:
        queries = dict(ecommerce_search_queries("SEL50F12GM", modifiers=["评测", "色散"]))
        self.assertNotIn("评测", queries["JD"])
        self.assertNotIn("色散", queries["Taobao/Tmall"])
        self.assertIn("site:item.jd.com", queries["JD"])

    def test_ecommerce_queries_prefer_item_hosts(self) -> None:
        queries = dict(ecommerce_search_queries("SEL50F12GM"))
        self.assertIn("site:item.jd.com", queries["JD"])
        self.assertIn("detail.tmall.com", queries["Taobao/Tmall"])
        self.assertNotIn("site:jd.com ", queries["JD"] + " ")

    def test_skips_campus_and_music_jd_urls(self) -> None:
        http = MagicMock()
        http.search.return_value = [
            SearchResult("校招", "https://campus.jd.com/", "校园招聘"),
            SearchResult("模板", "https://music.jd.com/8_0_desc_0_0_1_15.html?key={keyword}", "junk"),
            SearchResult("索尼 SEL50F12GM 镜头", "https://item.jd.com/100010708487.html", "到手价 SEL50F12GM"),
        ]
        http.fetch.side_effect = AssertionError("non-product URLs must not be fetched")

        registry = AdapterRegistry()
        registry.register(JdAdapter())
        registry.register(TmallTaobaoAdapter())
        collector = EcommerceSourceCollector(http=http, registry=registry, browser=MagicMock())
        # Bypass resilient network by stubbing fetch after filter
        fetched: list[str] = []

        def _fake_fetch(url, **kwargs):
            fetched.append(url)
            page = MagicMock()
            page.ok = True
            page.markup = "<html><body>到手价 12999</body></html>"
            page.text = "到手价 12999 元 标价 13999"
            page.screenshot_paths = []
            page.page.blockers = []
            page.url = url
            page.method = "http"
            return page

        collector.resilient.fetch = _fake_fetch  # type: ignore[method-assign]
        collector.jd.build_price_finding = MagicMock(return_value=None)  # type: ignore[method-assign]

        candidate = type("C", (), {"sku": "SEL50F12GM", "category": "Lens"})()
        collector.collect(candidate)  # type: ignore[arg-type]
        self.assertEqual(fetched, ["https://item.jd.com/100010708487.html"])

    def test_headed_captcha_only_for_product_urls(self) -> None:
        self.assertTrue(PlaywrightCapture.is_ecommerce_product_url("https://item.jd.com/1.html"))
        self.assertFalse(PlaywrightCapture.is_ecommerce_product_url("https://campus.jd.com/"))
        self.assertTrue(PlaywrightCapture.should_skip_headed_captcha("https://campus.jd.com/"))
        self.assertFalse(PlaywrightCapture.should_skip_headed_captcha("https://item.jd.com/1.html"))
        # Video / official hosts must never be treated as ecommerce junk.
        self.assertFalse(
            PlaywrightCapture.should_skip_headed_captcha("https://www.bilibili.com/video/BV1xx411c7mD")
        )
        self.assertFalse(
            PlaywrightCapture.should_skip_headed_captcha("https://www.youtube.com/watch?v=abc123")
        )
        self.assertFalse(
            PlaywrightCapture.should_skip_headed_captcha(
                "https://www.sony.com/electronics/support/lenses/sel50f12gm/specifications"
            )
        )

    def test_jd_frequency_control_skips_headed_even_for_product_request(self) -> None:
        freq = "https://pc-frequent-pro.pf.jd.com/?from=pc_item&reason=403"
        self.assertTrue(is_noisy_ecommerce_url(freq))
        self.assertTrue(
            PlaywrightCapture.should_skip_headed_captcha(
                "https://item.jd.com/100010708487.html",
                freq,
            )
        )

    def test_jd_mobile_product_path_is_product(self) -> None:
        adapter = JdAdapter()
        self.assertTrue(adapter.is_product_url("https://item.m.jd.com/product/100010708487.html"))
        self.assertEqual(
            adapter.normalize_url("https://item.m.jd.com/product/100010708487.html"),
            "https://item.jd.com/100010708487.html",
        )


if __name__ == "__main__":
    unittest.main()
