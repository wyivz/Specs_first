from __future__ import annotations

from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import build_evidence, dedupe_evidence, evidence_from_page, extract_price, platform_from_url
from collectors.http import HttpClient, clip
from collectors.resilient_fetch import ResilientFetcher
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
            evidence.extend(evidence_from_page(platform_from_url(page.url), page.url, page.markup, confidence=0.68))
        return dedupe_evidence(evidence)

    def collect_prices(
        self,
        urls: list[str],
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        prices: list[PriceFinding] = []
        for url in urls:
            page = self.resilient.fetch(
                url,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
            )
            if not page.ok and not page.markup:
                continue
            parsed = extract_price(page.text)
            if not parsed:
                continue
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
