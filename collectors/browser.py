from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class BrowserAuthRequired(RuntimeError):
    def __init__(self, message: str, url: str = "", storage_state_path: Path | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.storage_state_path = storage_state_path


@dataclass(frozen=True)
class BrowserCapture:
    url: str
    screenshot_paths: list[Path]
    page_text: str = ""
    storage_state_path: Path | None = None


class PlaywrightCapture:
    AUTH_MARKERS = ["captcha", "验证", "滑块", "安全检测", "verify you are human"]

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

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context_kwargs: dict = {"viewport": {"width": 1365, "height": self.slice_height}}
            if resolved_state.exists():
                context_kwargs["storage_state"] = str(resolved_state)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_load_state("networkidle", timeout=10_000)
                body_text = page.locator("body").inner_text(timeout=5_000)
                if self._needs_auth(body_text):
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
                    page.wait_for_timeout(300)
                    path = self.output_dir / f"{task_id}_slice_{index:03d}.png"
                    page.screenshot(path=str(path), full_page=False)
                    screenshots.append(path)
                context.storage_state(path=str(resolved_state))
                return BrowserCapture(
                    url=url,
                    screenshot_paths=screenshots,
                    page_text=body_text,
                    storage_state_path=resolved_state,
                )
            except PlaywrightTimeoutError as exc:
                context.storage_state(path=str(resolved_state))
                raise RuntimeError(f"Timed out while capturing {url}") from exc
            finally:
                browser.close()

    def _needs_auth(self, body_text: str) -> bool:
        lower = body_text.lower()
        return any(marker in lower for marker in self.AUTH_MARKERS)
