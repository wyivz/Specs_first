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

    def fetch(self, url: str) -> FetchResult:
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

    def test_invalid_url_returns_error(self) -> None:
        fetcher = ResilientFetcher(FakeHttp({}))  # type: ignore[arg-type]
        snapshot = fetcher.fetch("not-a-url")
        self.assertFalse(snapshot.ok)
        self.assertEqual(snapshot.error, "invalid url")


if __name__ == "__main__":
    unittest.main()
