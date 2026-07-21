from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.collection_trace import CollectionTrace
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import FetchResult, HttpClient, clip
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
    trace: CollectionTrace | None = None
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
        force_browser: bool = False,
    ) -> PageSnapshot:
        if self.trace:
            self.trace.log("fetch", f"start url={url} strategy={strategy_for_url(url).mode}", sku=sku)
        if self.diagnostics is not None and url.startswith("http"):
            self.diagnostics.record(
                "fetch",
                f"抓取中 {url}",
                level="info",
                sku=sku,
            )
        if not url.startswith("http"):
            return PageSnapshot(
                url=url,
                markup="",
                page=sanitize_html(url, ""),
                method="none",
                error="invalid url",
            )

        from collectors.rate_limit import get_host_backoff

        if get_host_backoff().should_skip_host(url):
            remaining = get_host_backoff().remaining_seconds(url)
            self.diagnostics.record(
                "fetch",
                f"skip host after repeated failures ({remaining:.0f}s left): {url}",
                level="warning",
                sku=sku,
            )
            skipped = PageSnapshot(
                url=url,
                markup="",
                page=sanitize_html(url, ""),
                method="skipped",
                error=f"host backoff ({remaining:.0f}s)",
            )
            self._log_snapshot(skipped, sku=sku)
            return skipped

        strategy = strategy_for_url(url)
        if force_browser and use_browser and self.browser is not None:
            try:
                browser_snapshot = self._fetch_browser(
                    url,
                    task_id=task_id,
                    storage_state_path=storage_state_path,
                    sku=sku,
                )
                self._log_snapshot(browser_snapshot, sku=sku)
                return browser_snapshot
            except BrowserAuthRequired:
                raise
            except Exception as exc:
                self.diagnostics.record(
                    "fetch",
                    f"forced browser fetch failed for {url}: {exc}",
                    level="warning",
                    sku=sku,
                )

        http_snapshot = self._fetch_http(url)
        needs_escalation = self._needs_browser_escalation(http_snapshot, strategy, requested_url=url)

        from collectors.url_guards import is_rate_limited_ecommerce_url
        from collectors.rate_limit import get_host_backoff

        if is_rate_limited_ecommerce_url(http_snapshot.url) or any(
            blocker.kind == "rate_limited" for blocker in http_snapshot.page.blockers
        ):
            cooldown = get_host_backoff().note_rate_limited(http_snapshot.url or url)
            self.diagnostics.record(
                "fetch",
                f"rate-limited ecommerce page; skipping browser escalation "
                f"(backoff {cooldown:.0f}s): {http_snapshot.url}",
                level="warning",
                sku=sku,
            )
            self._log_snapshot(http_snapshot, sku=sku)
            return http_snapshot

        # Sticky cooldown: do not reopen Playwright against a just-throttled host.
        if use_browser and get_host_backoff().in_backoff(url):
            remaining = get_host_backoff().remaining_seconds(url)
            self.diagnostics.record(
                "fetch",
                f"host backoff active ({remaining:.0f}s left); HTTP-only for {url}",
                level="info",
                sku=sku,
            )
            self._log_snapshot(http_snapshot, sku=sku)
            return http_snapshot

        # Hard HTTP-only when caller disables browser (CLI live / checkbox off).
        # Previously browser_first sites and weak pages still escalated to Playwright
        # and could hang Phase 2 on YouTube/Bilibili even with use_browser=False.
        if not use_browser:
            if http_snapshot.ok and not needs_escalation:
                self._log_snapshot(http_snapshot, sku=sku)
                return http_snapshot
            self.diagnostics.record(
                "fetch",
                f"http-only mode; not escalating to browser for {url}; strategy={strategy.mode}",
                level="info",
                sku=sku,
            )
            self._log_snapshot(http_snapshot, sku=sku)
            return http_snapshot

        force_browser_escalation = strategy.mode == "browser_first" or needs_escalation
        if strategy.mode == "api_first":
            force_browser_escalation = needs_escalation
        if http_snapshot.ok and not needs_escalation:
            self._log_snapshot(http_snapshot, sku=sku)
            return http_snapshot
        if needs_escalation and self.browser is not None:
            force_browser_escalation = True

        if http_snapshot.page.is_blocked and any(
            blocker.kind == "auth_or_captcha" for blocker in http_snapshot.page.blockers
        ):
            force_browser_escalation = True

        if not force_browser_escalation:
            if http_snapshot.ok and not needs_escalation:
                self._log_snapshot(http_snapshot, sku=sku)
                return http_snapshot
            self.diagnostics.record(
                "fetch",
                f"http fetch weak for {url}; strategy={strategy.mode}; blockers={http_snapshot.page.blockers}",
                level="info",
                sku=sku,
            )
            self._log_snapshot(http_snapshot, sku=sku)
            return http_snapshot

        try:
            browser_snapshot = self._fetch_browser(
                url,
                task_id=task_id,
                storage_state_path=storage_state_path,
                sku=sku,
            )
            if browser_snapshot.ok:
                self._log_snapshot(browser_snapshot, sku=sku)
                self._sync_taobao_session(storage_state_path)
                return browser_snapshot
            # Prefer thin browser product HTML over a fat HTTP homepage redirect.
            if self._product_url_redirected_away(url, http_snapshot.url) and browser_snapshot.markup:
                self.diagnostics.record(
                    "fetch",
                    f"preferring browser snapshot after product redirect for {url}",
                    level="info",
                    sku=sku,
                )
                self._log_snapshot(browser_snapshot, sku=sku)
                self._sync_taobao_session(storage_state_path)
                return browser_snapshot
            if http_snapshot.markup:
                self.diagnostics.record(
                    "fetch",
                    f"browser fallback still weak for {url}; using best-effort http snapshot",
                    level="warning",
                    sku=sku,
                )
                self._log_snapshot(http_snapshot, sku=sku)
                return http_snapshot
            self._log_snapshot(browser_snapshot, sku=sku)
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
            self._log_snapshot(http_snapshot, sku=sku)
            return http_snapshot

    def _log_snapshot(self, snapshot: PageSnapshot, *, sku: str = "") -> None:
        from collectors.rate_limit import get_host_backoff

        # low_signal alone should not burn the whole host family — soft articles /
        # short pages are common; captcha/http_blocked still trigger cooldown.
        soft_kinds = {"auth_or_captcha", "undecoded_content", "http_blocked"}
        if snapshot.ok:
            get_host_backoff().note_success(snapshot.url)
        else:
            for blocker in snapshot.blockers:
                if blocker.kind in soft_kinds:
                    cooldown = get_host_backoff().note_soft_failure(snapshot.url, blocker.kind)
                    if cooldown and self.diagnostics is not None:
                        self.diagnostics.record(
                            "fetch",
                            f"host soft-fail cooldown {cooldown:.0f}s after {blocker.kind}: {snapshot.url}",
                            level="info",
                            sku=sku,
                        )
                    break
        if not self.trace:
            return
        preview = clip(snapshot.text, 180)
        self.trace.log_fetch(
            snapshot.url,
            method=snapshot.method,
            status=snapshot.status,
            ok=snapshot.ok,
            text_len=len(snapshot.text),
            preview=preview,
            sku=sku,
            error=snapshot.error,
        )
        if snapshot.blockers:
            self.trace.log(
                "fetch",
                f"blockers={[f'{b.kind}:{b.detail}' for b in snapshot.blockers]}",
                sku=sku,
                level="warning",
            )

    def _fetch_http(self, url: str) -> PageSnapshot:
        from collectors.credentials import request_headers_for_url

        extra_headers = request_headers_for_url(url)
        result = self.http.fetch(url, extra_headers=extra_headers)
        final_url = result.url or url
        if not result.ok:
            # Keep body when present so rate_limited / captcha markers still detect.
            page = sanitize_html(final_url, result.text or "")
            if result.error and "undecoded" in result.error.lower():
                if not any(blocker.kind == "undecoded_content" for blocker in page.blockers):
                    page.blockers.append(PageBlocker("undecoded_content", result.error))
            if not any(blocker.kind == "http_error" for blocker in page.blockers):
                page.blockers.append(PageBlocker("http_error", result.error or f"HTTP {result.status}"))
            return PageSnapshot(
                url=final_url,
                markup=result.text,
                page=page,
                method="http",
                status=result.status,
                error=result.error or f"HTTP {result.status}",
            )
        page = sanitize_html(final_url, result.text)
        return PageSnapshot(
            url=final_url,
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

    def _sync_taobao_session(self, storage_state_path: str) -> None:
        if not storage_state_path:
            return
        try:
            from collectors.session_cache import sync_taobao_token_from_storage_state

            sync_taobao_token_from_storage_state(storage_state_path)
        except Exception:
            pass

    def _needs_browser_escalation(
        self,
        snapshot: PageSnapshot,
        strategy: SiteStrategy,
        *,
        requested_url: str = "",
    ) -> bool:
        from collectors.url_guards import is_rate_limited_ecommerce_url

        # JD pc-frequent-pro / rate_limited: never escalate — headed captcha cannot clear it.
        if is_rate_limited_ecommerce_url(snapshot.url) or any(
            blocker.kind == "rate_limited" for blocker in snapshot.blockers
        ):
            return False
        if snapshot.status in {403, 429, 503}:
            return True
        if len(snapshot.text.strip()) < max(strategy.min_chars, 80):
            return True
        if any(blocker.kind in {"auth_or_captcha", "http_blocked"} for blocker in snapshot.blockers):
            return True
        # item.jd.com often HTTP-redirects to www.jd.com homepage with a huge, "usable"
        # HTML body — still escalate so Playwright can load the real product page.
        if requested_url and self._product_url_redirected_away(requested_url, snapshot.url):
            return True
        if strategy.prefer_api and not (snapshot.page.json_ld or _contains_key_value_markup(snapshot.markup)):
            return True
        return not is_usable_page(snapshot.page, min_chars=strategy.min_chars)

    @staticmethod
    def _product_url_redirected_away(requested_url: str, final_url: str) -> bool:
        from collectors.browser import PlaywrightCapture
        from collectors.url_guards import is_noisy_ecommerce_url

        if not PlaywrightCapture.is_ecommerce_product_url(requested_url):
            return False
        if PlaywrightCapture.is_ecommerce_product_url(final_url):
            return False
        return is_noisy_ecommerce_url(final_url) or PlaywrightCapture.is_ecommerce_host(final_url)


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
