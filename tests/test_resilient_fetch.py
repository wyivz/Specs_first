from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path

from collectors.browser import BrowserCapture, BrowserAuthRequired
from collectors.http import FetchResult
from collectors.resilient_fetch import ResilientFetcher


@dataclass
class FakeHttp:
    pages: dict[str, str]
    status_by_url: dict[str, int] | None = None

    def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
        status_map = self.status_by_url or {}
        if url not in self.pages:
            return FetchResult(url=url, status=status_map.get(url, 404), text="", content_type="text/html", error="not found")
        return FetchResult(url=url, status=status_map.get(url, 200), text=self.pages[url], content_type="text/html")


class FakeBrowser:
    def __init__(self, capture: BrowserCapture | None = None, error: Exception | None = None) -> None:
        self.capture = capture
        self.error = error
        self.calls: list[str] = []

    def capture_page_slices(
        self,
        url: str,
        task_id: str = "manual",
        storage_state_path: Path | None = None,
    ) -> BrowserCapture:
        self.calls.append(url)
        if self.error:
            raise self.error
        assert self.capture is not None
        return self.capture


class ResilientFetcherTest(unittest.TestCase):
    def test_uses_http_when_page_is_usable(self) -> None:
        html = """
        <html><head><title>Specs</title></head>
        <body><main><p>Focal Length 50mm. Maximum Aperture f/2. Weight 530g.
        Minimum focus distance 0.24m. Filter thread 67mm. Optical structure 6 groups 8 elements.</p></main></body></html>
        """
        fetcher = ResilientFetcher(FakeHttp({"https://zeiss.example/specs": html}))  # type: ignore[arg-type]
        snapshot = fetcher.fetch("https://zeiss.example/specs")
        self.assertEqual(snapshot.method, "http")
        self.assertTrue(snapshot.ok)
        self.assertIn("Focal Length 50mm", snapshot.text)

    def test_falls_back_to_browser_for_jd_when_http_is_blocked(self) -> None:
        blocked = "<html><body><div class='captcha'>滑块验证</div></body></html>"
        browser_html = """
        <html><body><div class="sku-name">Sony FE 50mm GM</div>
        <div class="p-price">到手价 12999 元</div></body></html>
        """
        browser = FakeBrowser(
            BrowserCapture(
                url="https://item.jd.com/123.html",
                screenshot_paths=[],
                page_text=(
                    "Sony FE 50mm GM lens review. List price 13999 yuan. "
                    "Coupon discount 500 yuan. Subsidy 500 yuan. Final price 12999 yuan."
                ),
                page_html=browser_html,
            )
        )
        fetcher = ResilientFetcher(
            FakeHttp({"https://item.jd.com/123.html": blocked}),  # type: ignore[arg-type]
            browser=browser,  # type: ignore[arg-type]
        )
        snapshot = fetcher.fetch("https://item.jd.com/123.html", task_id="task-1")
        self.assertEqual(snapshot.method, "browser")
        self.assertIn("12999", snapshot.text)
        self.assertEqual(browser.calls, ["https://item.jd.com/123.html"])

    def test_propagates_browser_auth_required(self) -> None:
        browser = FakeBrowser(error=BrowserAuthRequired("login required", url="https://item.jd.com/123.html"))
        fetcher = ResilientFetcher(
            FakeHttp({"https://item.jd.com/123.html": "<html><body>login</body></html>"}),  # type: ignore[arg-type]
            browser=browser,  # type: ignore[arg-type]
        )
        with self.assertRaises(BrowserAuthRequired):
            fetcher.fetch("https://item.jd.com/123.html", use_browser=True)

    def test_http_first_for_forum_domain_without_browser_upgrade(self) -> None:
        html = (
            "<html><body><h1>Chiphell</h1><p>"
            "weight 530g battery life 6h compatibility sony e mount power draw 12W "
            "measured runtime 5.8h with stable performance and no crash."
            "</p></body></html>"
        )
        browser = FakeBrowser(
            BrowserCapture(
                url="https://www.chiphell.com/thread-1-1-1.html",
                screenshot_paths=[],
                page_text="browser fallback",
                page_html="<html><body>browser fallback</body></html>",
            )
        )
        fetcher = ResilientFetcher(
            FakeHttp({"https://www.chiphell.com/thread-1-1-1.html": html}),  # type: ignore[arg-type]
            browser=browser,  # type: ignore[arg-type]
        )
        snapshot = fetcher.fetch("https://www.chiphell.com/thread-1-1-1.html")
        self.assertEqual(snapshot.method, "http")
        self.assertEqual(browser.calls, [])

    def test_reddit_keeps_http_when_cookie_page_is_usable(self) -> None:
        html = (
            "<html><body><h1>r/SonyAlpha</h1><p>"
            "Sample variation and sticky focus ring reported by multiple owners after "
            "long-term use. Quality control issues and soft corners wide open."
            "</p></body></html>"
        )
        browser = FakeBrowser(
            BrowserCapture(
                url="https://www.reddit.com/r/SonyAlpha/comments/abc/",
                screenshot_paths=[],
                page_text="browser should not run",
                page_html="<html><body>browser</body></html>",
            )
        )
        fetcher = ResilientFetcher(
            FakeHttp({"https://www.reddit.com/r/SonyAlpha/comments/abc/": html}),  # type: ignore[arg-type]
            browser=browser,  # type: ignore[arg-type]
        )
        snapshot = fetcher.fetch("https://www.reddit.com/r/SonyAlpha/comments/abc/")
        self.assertEqual(snapshot.method, "http")
        self.assertIn("sticky focus ring", snapshot.text)
        self.assertEqual(browser.calls, [])

    def test_api_first_domain_escalates_when_http_payload_too_short(self) -> None:
        short_html = "<html><body>ok</body></html>"
        browser = FakeBrowser(
            BrowserCapture(
                url="https://detail.tmall.com/item.htm?id=1",
                screenshot_paths=[],
                page_text=(
                    "参数 重量 530g 续航 6h 功耗 12W 兼容性 Sony E "
                    "接口 USB-C 电池 78Wh 尺寸 120mm x 80mm x 20mm"
                ),
                page_html=(
                    "<html><body><table>"
                    "<tr><th>重量</th><td>530g</td></tr>"
                    "<tr><th>兼容性</th><td>Sony E</td></tr>"
                    "<tr><th>功耗</th><td>12W</td></tr>"
                    "</table></body></html>"
                ),
            )
        )
        fetcher = ResilientFetcher(
            FakeHttp({"https://detail.tmall.com/item.htm?id=1": short_html}),  # type: ignore[arg-type]
            browser=browser,  # type: ignore[arg-type]
        )
        snapshot = fetcher.fetch("https://detail.tmall.com/item.htm?id=1", task_id="task-2")
        self.assertIn(snapshot.method, {"browser", "http"})
        self.assertEqual(browser.calls, ["https://detail.tmall.com/item.htm?id=1"])

    def test_invalid_url_returns_error(self) -> None:
        fetcher = ResilientFetcher(FakeHttp({}))  # type: ignore[arg-type]
        snapshot = fetcher.fetch("not-a-url")
        self.assertFalse(snapshot.ok)
        self.assertEqual(snapshot.error, "invalid url")


if __name__ == "__main__":
    unittest.main()
