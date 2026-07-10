from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from collectors.embedded_browser import BrowserBridge, get_or_create_bridge, remove_bridge
from collectors.page_sanitize import AUTH_MARKERS, detect_page_blockers


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
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
    # Hosts where we attempt to solve captchas via headed browser before pausing
    HEADED_CAPTCHA_HOSTS = ("jd.com", "jd.hk", "taobao.com", "tmall.com")

    def __init__(
        self,
        output_dir: str | Path = "vault_output/browser_captures",
        slice_height: int = 2048,
        headed_fallback: bool = True,
        headed_timeout_seconds: int = 300,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.slice_height = slice_height
        self.headed_fallback = headed_fallback
        self.headed_timeout_seconds = headed_timeout_seconds

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
            self._inject_platform_cookies(context, url)
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
                    # For supported hosts, try to solve the captcha in a
                    # visible headed browser before fully pausing the pipeline.
                    needs_headed = self.headed_fallback and any(
                        host in url.lower() for host in self.HEADED_CAPTCHA_HOSTS
                    )
                    if needs_headed:
                        try:
                            return self._solve_captcha_headed(
                                playwright, url, resolved_state, user_agent, task_id
                            )
                        except BrowserAuthRequired:
                            raise
                        except Exception:
                            pass
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

    def _solve_captcha_headed(
        self,
        playwright,
        url: str,
        state_path: Path,
        user_agent: str,
        task_id: str,
    ) -> BrowserCapture:
        """Open a visible headed browser for the user to solve a captcha manually.

        Polls every 5 seconds for up to headed_timeout_seconds.  Once the
        captcha blocker clears, saves session state and returns a BrowserCapture
        identical to what the headless path would produce.  Raises
        BrowserAuthRequired if the user does not solve it in time.
        """
        self._notify_user_captcha(url)
        context_kwargs: dict = {
            "viewport": {"width": 390, "height": 844} if user_agent == MOBILE_UA else {"width": 1365, "height": self.slice_height},
            "user_agent": user_agent,
            "locale": "zh-CN",
        }
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)

        headed_browser = playwright.chromium.launch(headless=False)
        context = headed_browser.new_context(**context_kwargs)
        page = context.new_page()
        bridge = get_or_create_bridge(task_id, url=url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=35_000)

            deadline = time.monotonic() + self.headed_timeout_seconds
            solved = False
            last_blocker_check = 0.0
            while time.monotonic() < deadline:
                self._apply_bridge_commands(page, bridge)
                try:
                    bridge.publish_screenshot(page.screenshot(full_page=False))
                except Exception:
                    pass
                page.wait_for_timeout(1_000)

                now = time.monotonic()
                if now - last_blocker_check >= 2.0:
                    last_blocker_check = now
                    try:
                        html = page.content()
                        text = self._extract_main_text(page)
                        blockers = detect_page_blockers(page.url, html, text, page.title())
                        if not any(b.kind == "auth_or_captcha" for b in blockers):
                            solved = True
                            break
                    except Exception:
                        continue

            context.storage_state(path=str(state_path))
            if not solved:
                bridge.mark_error(f"Captcha not solved within {self.headed_timeout_seconds}s")
                raise BrowserAuthRequired(
                    f"Captcha not solved within {self.headed_timeout_seconds}s for {url}",
                    url=url,
                    storage_state_path=state_path,
                )

            bridge.mark_solved()
            self._dismiss_noise(page)
            page_html = page.content()
            body_text = self._extract_main_text(page)
            screenshots: list[Path] = []
            page_height = page.evaluate(
                "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )
            for index, y in enumerate(range(0, int(page_height), self.slice_height)):
                page.evaluate("(scrollY) => window.scrollTo(0, scrollY)", y)
                page.wait_for_timeout(250)
                path = self.output_dir / f"{task_id}_headed_slice_{index:03d}.png"
                page.screenshot(path=str(path), full_page=False)
                screenshots.append(path)
            return BrowserCapture(
                url=page.url,
                screenshot_paths=screenshots,
                page_text=body_text,
                page_html=page_html,
                storage_state_path=state_path,
            )
        finally:
            remove_bridge(task_id)
            headed_browser.close()

    def fetch_in_page_context(
        self,
        page_url: str,
        request_url: str,
        *,
        task_id: str = "api",
        storage_state_path: Path | None = None,
    ) -> str:
        """Open ``page_url`` in Playwright and ``fetch`` ``request_url`` with session cookies."""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install playwright and run `playwright install` before browser fetches.") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        resolved_state = storage_state_path or (self.output_dir / f"{task_id}_storage_state.json")
        user_agent = MOBILE_UA if any(host in page_url.lower() for host in self.MOBILE_HOSTS) else DESKTOP_UA

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
            self._inject_platform_cookies(context, page_url)
            page = context.new_page()
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=35_000)
                page.wait_for_timeout(900)
                text = page.evaluate(
                    """async (url) => {
                        const resp = await fetch(url, { credentials: 'include', mode: 'cors' });
                        return await resp.text();
                    }""",
                    request_url,
                )
                context.storage_state(path=str(resolved_state))
                return text or ""
            except PlaywrightTimeoutError as exc:
                raise RuntimeError(f"Timed out while fetching {request_url} via browser") from exc
            finally:
                browser.close()

    @staticmethod
    def _inject_platform_cookies(context, url: str) -> None:
        from collectors.credentials import playwright_cookies_for_url

        cookies = playwright_cookies_for_url(url)
        if not cookies:
            return
        try:
            context.add_cookies(cookies)
        except Exception:
            pass

    @staticmethod
    def _apply_bridge_commands(page, bridge: BrowserBridge) -> None:
        """Replay UI-submitted click/type/key/scroll commands onto the live page.

        Lets the Streamlit (or any REST) client drive this headed browser
        via the ``BrowserBridge`` mailbox instead of requiring the user to
        alt-tab into the separate OS window.
        """
        for command in bridge.drain_commands():
            try:
                if command.action == "click":
                    page.mouse.click(command.kwargs.get("x", 0), command.kwargs.get("y", 0))
                elif command.action == "type":
                    page.keyboard.type(command.kwargs.get("text", ""), delay=30)
                elif command.action == "key":
                    page.keyboard.press(command.kwargs.get("key", "Enter"))
                elif command.action == "scroll":
                    page.mouse.wheel(0, command.kwargs.get("delta", 400))
            except Exception:
                continue

    @staticmethod
    def _notify_user_captcha(url: str) -> None:
        """Emit a desktop notification / beep so the user knows to look at the browser."""
        import sys

        msg = (
            f"\n[Specs-First] Captcha detected on {url}\n"
            "A headed browser has opened — please solve the verification challenge.\n"
            "Waiting up to 5 minutes...\n"
        )
        print(msg, flush=True)
        try:
            if sys.platform == "win32":
                import winsound
                for _ in range(3):
                    winsound.Beep(880, 300)
                    time.sleep(0.15)
        except Exception:
            pass

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
