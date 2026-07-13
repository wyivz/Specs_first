from __future__ import annotations

from collectors.adapters.jd import JdAdapter
from collectors.collection_trace import CollectionTrace
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import build_evidence, dedupe_evidence, evidence_from_page, extract_price, platform_from_url
from collectors.http import HttpClient, clip
from collectors.resilient_fetch import ResilientFetcher
from collectors.url_guards import is_noisy_ecommerce_url
from schemas import EvidenceItem, PriceFinding


class UrlInjectionCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)
        self.jd = JdAdapter()

    def collect_evidence(
        self,
        urls: list[str],
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
        sku: str = "",
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for url in urls:
            page = self.resilient.fetch(
                url,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
                sku=sku,
            )
            if not page.ok and not page.markup:
                self.diagnostics.record("url", f"failed to fetch {url}: {page.error}", sku=sku)
                continue
            evidence.extend(
                evidence_from_page(
                    platform_from_url(page.url),
                    page.url,
                    page.markup,
                    confidence=0.68,
                    sku=sku,
                )
            )
        return dedupe_evidence(evidence)

    def collect_prices(
        self,
        urls: list[str],
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
        trace: CollectionTrace | None = None,
        sku: str = "",
    ) -> list[PriceFinding]:
        prices: list[PriceFinding] = []
        active_trace = trace or self.resilient.trace
        for url in urls:
            if active_trace:
                active_trace.log("injection", f"price url={url}", sku=sku)
            # Official/manual pages are for specs, not price scraping.
            if not self._is_price_candidate_url(url):
                self.diagnostics.record(
                    "url",
                    f"skip non-commerce url for price injection: {url}",
                    level="info",
                    sku=sku,
                )
                continue
            page = self.resilient.fetch(
                url,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
                sku=sku,
            )
            if not page.ok and not page.markup and not self.jd.is_product_url(url):
                continue
            if self.jd.is_product_url(url):
                markup = page.markup if self.jd.is_product_url(page.url) else ""
                jd_finding = self.jd.build_price_finding(
                    url,
                    markup,
                    platform="JD",
                    http=self.http,
                    trace=active_trace,
                    sku=sku,
                )
                if jd_finding:
                    prices.append(jd_finding)
                elif is_noisy_ecommerce_url(page.url):
                    self.diagnostics.record(
                        "JD",
                        f"skip redirected non-product price page: {url} -> {page.url}",
                        level="warning",
                        sku=sku,
                    )
                continue
            if is_noisy_ecommerce_url(page.url) or not self._is_price_candidate_url(page.url):
                self.diagnostics.record(
                    "url",
                    f"skip redirected/non-product price page: {url} -> {page.url}",
                    level="warning",
                    sku=sku,
                )
                continue
            parsed = extract_price(page.text)
            if not parsed:
                continue
            if active_trace:
                active_trace.log_price(
                    platform_from_url(page.url),
                    page.url,
                    source=f"text-{page.method}",
                    list_price=parsed.list_price,
                    final_price=parsed.final_price,
                    sku=sku,
                )
            evidence = build_evidence(
                platform=platform_from_url(page.url),
                url=page.url,
                author=platform_from_url(page.url),
                locator=f"injected-url-price-{page.method}",
                excerpt=clip(page.text, 360),
                confidence=0.68,
            )
            prices.append(
                PriceFinding(
                    platform=platform_from_url(page.url),
                    list_price=parsed.list_price,
                    coupon_discount=parsed.coupon_discount,
                    subsidy_discount=parsed.subsidy_discount,
                    cross_store_discount=parsed.cross_store_discount,
                    final_price=parsed.final_price,
                    screenshot_path="",
                    captured_at=evidence.captured_at,
                    evidence=evidence,
                )
            )
        return sorted(prices, key=lambda item: item.final_price)

    @staticmethod
    def _is_price_candidate_url(url: str) -> bool:
        lower = (url or "").lower()
        return any(
            hint in lower
            for hint in (
                "item.jd.com/",
                "item.m.jd.com/",
                "item.taobao.com/",
                "detail.tmall.com/",
            )
        )
