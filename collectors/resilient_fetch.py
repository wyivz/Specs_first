from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import FetchResult, HttpClient
from collectors.page_sanitize import PageBlocker, SanitizedPage, is_usable_page, sanitize_html


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    markup: str
    page: SanitizedPage
    method: str
    status: int = 0
    error: str = ""
    screenshot_paths: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.error and is_usable_page(self.page)

    @property
    def text(self) -> str:
        return self.page.rich_text

    @property
    def blockers(self) -> list[PageBlocker]:
        return self.page.blockers


@dataclass
class ResilientFetcher:
    http: HttpClient
    browser: PlaywrightCapture | None = None
    diagnostics: CollectorDiagnostics | None = None
    prefer_browser_hosts: tuple[str, ...] = (
        "jd.com",
        "jd.hk",
        "taobao.com",
        "tmall.com",
        "bilibili.com",
        "chiphell.com",
    )

    def __post_init__(self) -> None:
        self.browser = self.browser or PlaywrightCapture()
        self.diagnostics = self.diagnostics or CollectorDiagnostics()

    def fetch(
        self,
        url: str,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
        sku: str = "",
        min_chars: int = 80,
    ) -> PageSnapshot:
        if not url.startswith("http"):
            return PageSnapshot(
                url=url,
                markup="",
                page=sanitize_html(url, ""),
                method="none",
                error="invalid url",
            )

        force_browser = use_browser or self._should_prefer_browser(url)
        http_snapshot = self._fetch_http(url)
        if http_snapshot.ok and not force_browser:
            return http_snapshot

        if http_snapshot.page.is_blocked and any(
            blocker.kind == "auth_or_captcha" for blocker in http_snapshot.page.blockers
        ):
            force_browser = True

        if not force_browser:
            if http_snapshot.ok:
                return http_snapshot
            self.diagnostics.record(
                "fetch",
                f"http fetch weak for {url}; blockers={http_snapshot.page.blockers}",
                level="info",
                sku=sku,
            )
            return http_snapshot

        try:
            browser_snapshot = self._fetch_browser(
                url,
                task_id=task_id,
                storage_state_path=storage_state_path,
                sku=sku,
            )
            if browser_snapshot.ok:
                return browser_snapshot
            if http_snapshot.markup:
                self.diagnostics.record(
                    "fetch",
                    f"browser fallback still weak for {url}; using best-effort http snapshot",
                    level="warning",
                    sku=sku,
                )
                return http_snapshot
            return browser_snapshot
        except BrowserAuthRequired:
            raise
        except Exception as exc:
            self.diagnostics.record(
                "fetch",
                f"browser fetch failed for {url}: {exc}",
                level="warning",
                sku=sku,
            )
            return http_snapshot

    def _fetch_http(self, url: str) -> PageSnapshot:
        result = self.http.fetch(url)
        if not result.ok:
            page = sanitize_html(url, "")
            page.blockers.append(PageBlocker("http_error", result.error or f"HTTP {result.status}"))
            return PageSnapshot(
                url=result.url,
                markup=result.text,
                page=page,
                method="http",
                status=result.status,
                error=result.error or f"HTTP {result.status}",
            )
        page = sanitize_html(result.url, result.text)
        return PageSnapshot(
            url=result.url,
            markup=result.text,
            page=page,
            method="http",
            status=result.status,
        )

    def _fetch_browser(
        self,
        url: str,
        *,
        task_id: str,
        storage_state_path: str,
        sku: str,
    ) -> PageSnapshot:
        assert self.browser is not None
        capture = self.browser.capture_page_slices(
            url,
            task_id=task_id or "manual",
            storage_state_path=Path(storage_state_path) if storage_state_path else None,
        )
        markup = capture.page_html or f"<html><body>{capture.page_text}</body></html>"
        page = sanitize_html(capture.url, markup)
        if capture.page_text and len(page.text) < len(capture.page_text):
            page = SanitizedPage(
                url=capture.url,
                title=page.title,
                text=capture.page_text,
                json_ld=page.json_ld,
                meta_description=page.meta_description,
                blockers=page.blockers,
            )
        return PageSnapshot(
            url=capture.url,
            markup=markup,
            page=page,
            method="browser",
            screenshot_paths=tuple(str(path) for path in capture.screenshot_paths),
        )

    def _should_prefer_browser(self, url: str) -> bool:
        lower = url.lower()
        return any(host in lower for host in self.prefer_browser_hosts)
