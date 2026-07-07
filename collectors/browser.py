from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from collectors.page_sanitize import AUTH_MARKERS, detect_page_blockers


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 SpecsFirst/0.2"
)


@dataclass(frozen=True)
class BrowserCapture:
    url: str
    screenshot_paths: list[Path]
    page_text: str = ""
    page_html: str = ""
    storage_state_path: Path | None = None


class BrowserAuthRequired(RuntimeError):
    def __init__(self, message: str, url: str = "", storage_state_path: Path | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.storage_state_path = storage_state_path
        self.in_progress_payload: dict | None = None


class PlaywrightCapture:
    AUTH_MARKERS = AUTH_MARKERS
    MOBILE_HOSTS = ("jd.com", "jd.hk", "taobao.com", "tmall.com")

    def __init__(self, output_dir: str | Path = "vault_output/browser_captures", slice_height: int = 2048) -> None:
        self.output_dir = Path(output_dir)
        self.slice_height = slice_height

    def capture_page_slices(
        self,
        url: str,
        task_id: str = "manual",
        storage_state_path: Path | None = None,
    ) -> BrowserCapture:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install playwright and run `playwright install` before browser captures.") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        screenshots: list[Path] = []
        resolved_state = storage_state_path or (self.output_dir / f"{task_id}_storage_state.json")
        user_agent = MOBILE_UA if any(host in url.lower() for host in self.MOBILE_HOSTS) else DESKTOP_UA

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context_kwargs: dict = {
                "viewport": {"width": 390, "height": 844} if user_agent == MOBILE_UA else {"width": 1365, "height": self.slice_height},
                "user_agent": user_agent,
                "locale": "zh-CN",
            }
            if resolved_state.exists():
                context_kwargs["storage_state"] = str(resolved_state)
            context = browser.new_context(**context_kwargs)
            context.route("**/*", self._route_filter)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                page.wait_for_timeout(1200)
                self._dismiss_noise(page)
                page_html = page.content()
                body_text = self._extract_main_text(page)
                blockers = detect_page_blockers(url, page_html, body_text, page.title())
                if any(blocker.kind == "auth_or_captcha" for blocker in blockers):
                    context.storage_state(path=str(resolved_state))
                    raise BrowserAuthRequired(
                        f"Authentication challenge detected for {url}",
                        url=url,
                        storage_state_path=resolved_state,
                    )

                page_height = page.evaluate(
                    "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
                )
                for index, y in enumerate(range(0, int(page_height), self.slice_height)):
                    page.evaluate("(scrollY) => window.scrollTo(0, scrollY)", y)
                    page.wait_for_timeout(250)
                    path = self.output_dir / f"{task_id}_slice_{index:03d}.png"
                    page.screenshot(path=str(path), full_page=False)
                    screenshots.append(path)
                context.storage_state(path=str(resolved_state))
                return BrowserCapture(
                    url=page.url,
                    screenshot_paths=screenshots,
                    page_text=body_text,
                    page_html=page_html,
                    storage_state_path=resolved_state,
                )
            except PlaywrightTimeoutError as exc:
                context.storage_state(path=str(resolved_state))
                raise RuntimeError(f"Timed out while capturing {url}") from exc
            finally:
                browser.close()

    def _route_filter(self, route, request) -> None:
        if request.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()

    def _dismiss_noise(self, page) -> None:
        selectors = [
            "button:has-text('Accept')",
            "button:has-text('同意')",
            "button:has-text('关闭')",
            "[aria-label='Close']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=800)
                    page.wait_for_timeout(300)
            except Exception:
                continue
        page.add_style_tag(
            content=(
                "nav, footer, aside, .ad, .ads, .cookie, .popup, .modal, iframe {"
                "visibility:hidden !important; height:0 !important; overflow:hidden !important;}"
            )
        )

    def _extract_main_text(self, page) -> str:
        selectors = [
            "article",
            "main",
            "#content",
            ".product-intro",
            ".itemInfo-wrap",
            ".sku-name",
            ".p-price",
            ".comment-list",
            ".video-desc",
            ".desc-content",
        ]
        chunks: list[str] = []
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                text = locator.inner_text(timeout=1200)
                if text and len(text.strip()) > 20:
                    chunks.append(text.strip())
            except Exception:
                continue
        if chunks:
            return "\n".join(chunks)
        try:
            return page.locator("body").inner_text(timeout=4000)
        except Exception:
            return ""
