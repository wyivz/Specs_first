from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from collectors.detail_images import (
    download_detail_image,
    image_request_headers,
    infer_image_referer,
)
from collectors.extractors import extract_detail_image_urls, rank_detail_image_urls


class DetailImageExtractTest(unittest.TestCase):
    def test_extracts_html_src_and_json_image_url(self) -> None:
        markup = """
        <img data-lazyload="//img14.360buyimg.com/n1/s800x800_jfs/t1/abc/参数图.jpg" />
        <script>var data={"imageUrl":"https://img.alicdn.com/imgextra/i1/123/O1CN01spec.png"};</script>
        <img src="https://img14.360buyimg.com/n5/s50x50_jfs/t1/icon.jpg" />
        """
        urls = extract_detail_image_urls(markup)
        self.assertTrue(any("参数图" in u or "n1/s800" in u for u in urls))
        self.assertTrue(any("alicdn.com" in u for u in urls))
        # Spec/detail assets should rank above tiny thumbs when both present.
        if len(urls) >= 2:
            self.assertTrue(
                urls[0].find("参数") >= 0
                or "/n1/" in urls[0]
                or "alicdn.com" in urls[0]
                or "800" in urls[0]
            )

    def test_rank_prefers_spec_over_icon(self) -> None:
        ranked = rank_detail_image_urls(
            [
                "https://cdn.example/icon_logo_s40x40.png",
                "https://cdn.example/detail_参数_规格_800x800.jpg",
                "https://cdn.example/avatar.png",
            ]
        )
        self.assertIn("参数", ranked[0])

    def test_infer_referer_for_jd_cdn(self) -> None:
        self.assertEqual(
            infer_image_referer("https://img14.360buyimg.com/n1/foo.jpg"),
            "https://item.jd.com/",
        )
        self.assertEqual(
            infer_image_referer(
                "https://img.alicdn.com/imgextra/i1/x.png",
                "https://detail.tmall.com/item.htm?id=1",
            ),
            "https://detail.tmall.com/item.htm?id=1",
        )

    def test_image_headers_include_referer(self) -> None:
        headers = image_request_headers(
            "https://img14.360buyimg.com/n1/foo.jpg",
            referer="https://item.jd.com/123.html",
        )
        self.assertEqual(headers.get("Referer"), "https://item.jd.com/123.html")
        self.assertIn("Mozilla", headers.get("User-Agent", ""))

    def test_download_retries_and_returns_none_on_failure(self) -> None:
        with patch("collectors.detail_images.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(
                download_detail_image("https://img14.360buyimg.com/n1/foo.jpg", attempts=2)
            )

    def test_download_sniffs_jpeg_magic(self) -> None:
        class FakeResp:
            headers = MagicMock()

            def read(self, _n: int) -> bytes:
                return b"\xff\xd8\xff" + b"0" * 300

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        FakeResp.headers.get_content_type.return_value = "application/octet-stream"
        with patch("collectors.detail_images.urlopen", return_value=FakeResp()):
            downloaded = download_detail_image("https://img14.360buyimg.com/n1/foo.jpg")
        assert downloaded is not None
        self.assertEqual(downloaded.mime_type, "image/jpeg")
        self.assertGreaterEqual(len(downloaded.data), 256)

    def test_load_local_file_uri(self) -> None:
        import tempfile
        from pathlib import Path

        from collectors.detail_images import load_local_image, path_to_file_url

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "param_01.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 300)
            file_url = path_to_file_url(path)
            loaded = load_local_image(file_url)
            assert loaded is not None
            self.assertEqual(loaded.mime_type, "image/png")
            via_download = download_detail_image(file_url)
            assert via_download is not None
            self.assertEqual(via_download.mime_type, "image/png")

    def test_probe_appends_param_screenshot_file_urls(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        from collectors.adapters.jd import JdAdapter
        from collectors.adapters.registry import AdapterRegistry
        from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
        from collectors.http import SearchResult
        from collectors.sources.ecommerce import EcommerceSourceCollector

        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "schema-probe_param_00.png"
            shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 400)
            http = MagicMock()
            http.search.return_value = [
                SearchResult("SEL50F12GM", "https://item.jd.com/100010708487.html", "SEL50F12GM"),
            ]
            registry = AdapterRegistry()
            registry.register(JdAdapter())
            registry.register(TmallTaobaoAdapter())
            browser = MagicMock()
            browser.capture_param_region_shots.return_value = [shot]
            collector = EcommerceSourceCollector(http=http, registry=registry, browser=browser)

            page = MagicMock()
            page.ok = True
            page.markup = "<html><body>商品页无详情图</body></html>"
            page.text = "商品页"
            page.url = "https://item.jd.com/100010708487.html"
            page.page.blockers = []
            page.page.title = "SEL50F12GM"
            page.screenshot_paths = []
            page.method = "http"
            collector.resilient.fetch = MagicMock(return_value=page)  # type: ignore[method-assign]
            collector._fetch_detail_payloads = MagicMock(return_value=[])  # type: ignore[method-assign]

            candidate = type("C", (), {"sku": "SEL50F12GM", "category": "Lens"})()
            urls = collector.probe_detail_images(candidate, use_browser=True, max_images=4)  # type: ignore[arg-type]
            self.assertTrue(any(u.startswith("file:") for u in urls))
            browser.capture_param_region_shots.assert_called()


if __name__ == "__main__":
    unittest.main()
