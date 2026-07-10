from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import FetchResult, HttpClient
from collectors.page_sanitize import PageBlocker, SanitizedPage, is_usable_page, sanitize_html
from collectors.site_strategy import SiteStrategy, strategy_for_url


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
    site_strategies: tuple[SiteStrategy, ...] = field(default_factory=tuple)

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

        strategy = strategy_for_url(url)
        force_browser = use_browser or strategy.mode == "browser_first"
        if strategy.mode == "api_first":
            force_browser = use_browser
        http_snapshot = self._fetch_http(url)
        needs_escalation = self._needs_browser_escalation(http_snapshot, strategy)
        if http_snapshot.ok and not needs_escalation:
            return http_snapshot
        if needs_escalation and self.browser is not None:
            force_browser = True

        if http_snapshot.page.is_blocked and any(
            blocker.kind == "auth_or_captcha" for blocker in http_snapshot.page.blockers
        ):
            force_browser = True

        if not force_browser:
            if http_snapshot.ok and not needs_escalation:
                return http_snapshot
            self.diagnostics.record(
                "fetch",
                f"http fetch weak for {url}; strategy={strategy.mode}; blockers={http_snapshot.page.blockers}",
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
        from collectors.credentials import request_headers_for_url

        extra_headers = request_headers_for_url(url)
        result = self.http.fetch(url, extra_headers=extra_headers)
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

    def _needs_browser_escalation(self, snapshot: PageSnapshot, strategy: SiteStrategy) -> bool:
        if snapshot.status in {403, 429, 503}:
            return True
        if len(snapshot.text.strip()) < max(strategy.min_chars, 80):
            return True
        if any(blocker.kind in {"auth_or_captcha", "http_blocked"} for blocker in snapshot.blockers):
            return True
        if strategy.prefer_api and not (snapshot.page.json_ld or _contains_key_value_markup(snapshot.markup)):
            return True
        return not is_usable_page(snapshot.page, min_chars=strategy.min_chars)


def _contains_key_value_markup(markup: str) -> bool:
    lower = markup.lower()
    return any(
        token in lower
        for token in (
            "<table",
            "spec",
            "参数",
            "technical details",
            "product details",
        )
    )
