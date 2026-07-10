from __future__ import annotations

from collectors.adapters.jd import JdAdapter
from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.collection_trace import CollectionTrace, create_collection_trace
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    build_evidence,
    extract_desc_api_urls,
    extract_detail_image_urls,
    extract_price,
    extract_specs_from_markup,
    platform_from_url,
)
from collectors.http import HttpClient, clip
from collectors.platform_auth import PlatformAuthRequired
from collectors.protocols import SpecExtractionRouter
from collectors.resilient_fetch import ResilientFetcher
from schemas import OfficialSpec, PriceFinding, ProductCandidate
from schemas.category_profile import ecommerce_search_queries


class EcommerceSourceCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        browser: PlaywrightCapture | None = None,
        resilient: ResilientFetcher | None = None,
        *,
        registry: AdapterRegistry | None = None,
        router: SpecExtractionRouter | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.browser = browser or PlaywrightCapture()
        self.resilient = resilient or ResilientFetcher(http, self.browser, self.diagnostics)
        self.registry = registry or create_default_registry(http=http, diagnostics=self.diagnostics)
        self.router = router
        self.jd = self.registry.require(JdAdapter)
        self.tmall_taobao = self.registry.require(TmallTaobaoAdapter)

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
        trace: CollectionTrace | None = None,
    ) -> list[PriceFinding]:
        findings: list[PriceFinding] = []
        active_trace = trace or self.resilient.trace
        for platform, query in ecommerce_search_queries(candidate.sku):
            if active_trace:
                active_trace.log("ecommerce", f"search platform={platform} query={query}", sku=candidate.sku)
            for result in self.http.search(query, max_results=5):
                adapter = self.registry.for_platform(platform)
                if adapter is not None and hasattr(adapter, "normalize_url"):
                    target_url = adapter.normalize_url(result.url)
                else:
                    target_url = result.url
                combined_text = f"{result.title}. {result.snippet}"
                try:
                    snapshot = self.resilient.fetch(
                        target_url,
                        task_id=task_id,
                        use_browser=use_browser or platform in {"JD", "Taobao/Tmall"},
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                except (BrowserAuthRequired, PlatformAuthRequired):
                    raise
                if platform == "Taobao/Tmall":
                    self.tmall_taobao.maybe_raise_page_auth(
                        snapshot.text,
                        snapshot.page.blockers,
                        snapshot.url,
                    )
                screenshot_paths = list(snapshot.screenshot_paths)
                combined_text = f"{combined_text} {snapshot.text}"
                if platform == "JD" and snapshot.markup:
                    jd_finding = self.jd.build_price_finding(
                        snapshot.url,
                        snapshot.markup,
                        platform="JD",
                        http=self.http,
                        trace=active_trace,
                        sku=candidate.sku,
                    )
                    if jd_finding:
                        findings.append(
                            PriceFinding(
                                platform=jd_finding.platform,
                                list_price=jd_finding.list_price,
                                coupon_discount=jd_finding.coupon_discount,
                                subsidy_discount=jd_finding.subsidy_discount,
                                cross_store_discount=jd_finding.cross_store_discount,
                                final_price=jd_finding.final_price,
                                screenshot_path=",".join(screenshot_paths),
                                captured_at=jd_finding.captured_at,
                                evidence=jd_finding.evidence,
                            )
                        )
                        continue
                if platform == "Taobao/Tmall" and snapshot.markup:
                    tb_finding = self.tmall_taobao.build_price_finding(
                        snapshot.url, snapshot.markup, platform="Taobao/Tmall"
                    )
                    if tb_finding:
                        findings.append(
                            PriceFinding(
                                platform=tb_finding.platform,
                                list_price=tb_finding.list_price,
                                coupon_discount=tb_finding.coupon_discount,
                                subsidy_discount=tb_finding.subsidy_discount,
                                cross_store_discount=tb_finding.cross_store_discount,
                                final_price=tb_finding.final_price,
                                screenshot_path=",".join(screenshot_paths),
                                captured_at=tb_finding.captured_at,
                                evidence=tb_finding.evidence,
                            )
                        )
                        continue
                    detail_urls = self.tmall_taobao.detail_api_urls(snapshot.url, snapshot.markup)
                    for detail_url in detail_urls[:2]:
                        if "sign=" not in detail_url:
                            continue
                        try:
                            raw = self.tmall_taobao.fetch_mtop_payload(
                                self.http,
                                detail_url,
                                referer=snapshot.url,
                                browser=self.browser,
                                task_id=task_id,
                                storage_state_path=storage_state_path,
                                use_browser=use_browser,
                            )
                        except PlatformAuthRequired:
                            raise
                        tb_finding = self.tmall_taobao.build_price_finding(
                            snapshot.url, raw, platform="Taobao/Tmall"
                        )
                        if tb_finding:
                            findings.append(
                                PriceFinding(
                                    platform=tb_finding.platform,
                                    list_price=tb_finding.list_price,
                                    coupon_discount=tb_finding.coupon_discount,
                                    subsidy_discount=tb_finding.subsidy_discount,
                                    cross_store_discount=tb_finding.cross_store_discount,
                                    final_price=tb_finding.final_price,
                                    screenshot_path=",".join(screenshot_paths),
                                    captured_at=tb_finding.captured_at,
                                    evidence=tb_finding.evidence,
                                )
                            )
                            break
                    if findings and findings[-1].platform == "Taobao/Tmall":
                        continue
                if not snapshot.ok:
                    self.diagnostics.record(
                        platform,
                        f"weak ecommerce snapshot for {target_url}: {snapshot.error or snapshot.page.blockers}",
                        level="warning",
                        sku=candidate.sku,
                    )
                parsed = extract_price(combined_text)
                if not parsed:
                    if active_trace:
                        active_trace.log_price(
                            platform,
                            snapshot.url,
                            source="text",
                            detail="no price parsed",
                            sku=candidate.sku,
                        )
                    self.diagnostics.record(
                        platform,
                        f"no price parsed for {target_url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                if active_trace:
                    active_trace.log_price(
                        platform,
                        snapshot.url,
                        source=f"text-{snapshot.method}",
                        list_price=parsed.list_price,
                        final_price=parsed.final_price,
                        sku=candidate.sku,
                    )
                evidence = build_evidence(
                    platform=platform_from_url(target_url) or platform,
                    url=snapshot.url,
                    author=platform,
                    locator=f"price-text-{snapshot.method}",
                    excerpt=clip(combined_text, 360),
                    confidence=0.68 if snapshot.method == "browser" else 0.55,
                )
                findings.append(
                    PriceFinding(
                        platform=platform,
                        list_price=parsed.list_price,
                        coupon_discount=parsed.coupon_discount,
                        subsidy_discount=parsed.subsidy_discount,
                        cross_store_discount=parsed.cross_store_discount,
                        final_price=parsed.final_price,
                        screenshot_path=",".join(screenshot_paths),
                        captured_at=evidence.captured_at,
                        evidence=evidence,
                    )
                )
        return sorted(findings, key=lambda item: item.final_price)[:5]

    def collect_official_specs(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        specs_by_name: dict[str, OfficialSpec] = {}
        highlights: list[str] = []
        for platform, query in ecommerce_search_queries(candidate.sku):
            for result in self.http.search(query, max_results=4):
                adapter = self.registry.for_platform(platform)
                if adapter is not None and hasattr(adapter, "normalize_url"):
                    target_url = adapter.normalize_url(result.url)
                else:
                    target_url = result.url
                try:
                    snapshot = self.resilient.fetch(
                        target_url,
                        task_id=task_id,
                        use_browser=use_browser or platform == "Taobao/Tmall",
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                except (BrowserAuthRequired, PlatformAuthRequired):
                    raise
                if platform == "Taobao/Tmall":
                    self.tmall_taobao.maybe_raise_page_auth(
                        snapshot.text,
                        snapshot.page.blockers,
                        snapshot.url,
                    )
                if not snapshot.markup:
                    continue
                detail_api_urls = self._detail_api_urls_for_platform(platform, snapshot.url, snapshot.markup)
                detail_payloads = self._fetch_detail_payloads(
                    detail_api_urls,
                    platform=platform,
                    referer_url=snapshot.url,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                merged_markup = "\n".join([snapshot.markup, *detail_payloads])
                extracted = extract_specs_from_markup(merged_markup, snapshot.url, candidate.category)
                for spec in extracted:
                    specs_by_name.setdefault(spec.name, spec)
                detail_images = extract_detail_image_urls(merged_markup)
                if detail_images and len(highlights) < 3:
                    highlights.append(f"{platform} detail images: {len(detail_images)}")
                if self.router is not None:
                    image_specs, image_highlights = self.router.extract_official_specs_from_images(
                        candidate.sku,
                        detail_images,
                        snapshot.url,
                        category=candidate.category,
                    )
                    for spec in image_specs:
                        specs_by_name.setdefault(spec.name, spec)
                    for item in image_highlights:
                        if item not in highlights and len(highlights) < 5:
                            highlights.append(item)
                if specs_by_name and len(highlights) < 5:
                    highlights.append(f"{platform} parameter block captured")
        return list(specs_by_name.values()), highlights

    def _detail_api_urls_for_platform(self, platform: str, url: str, markup: str) -> list[str]:
        if platform == "JD":
            return self.jd.detail_api_urls(url, markup)
        urls = extract_desc_api_urls(markup, url)
        if platform == "Taobao/Tmall":
            adapter_urls = self.tmall_taobao.detail_api_urls(url, markup)
            return list(dict.fromkeys([*adapter_urls, *urls]))[:6]
        return urls[:3]

    def _fetch_detail_payloads(
        self,
        urls: list[str],
        *,
        platform: str = "",
        referer_url: str = "",
        task_id: str,
        use_browser: bool,
        storage_state_path: str,
        sku: str,
    ) -> list[str]:
        payloads: list[str] = []
        for detail_url in urls[:6]:
            if platform == "Taobao/Tmall" and "mtop." in detail_url and self.tmall_taobao.credentials.configured:
                try:
                    raw = self.tmall_taobao.fetch_mtop_payload(
                        self.http,
                        detail_url,
                        referer=referer_url or detail_url,
                        browser=self.browser,
                        task_id=task_id,
                        storage_state_path=storage_state_path,
                        use_browser=use_browser,
                    )
                except PlatformAuthRequired:
                    raise
                if raw:
                    payloads.append(self._unwrap_detail_payload(platform, raw))
                continue
            try:
                snapshot = self.resilient.fetch(
                    detail_url,
                    task_id=task_id,
                    use_browser=use_browser,
                    storage_state_path=storage_state_path,
                    sku=sku,
                )
            except (BrowserAuthRequired, PlatformAuthRequired):
                raise
            raw = snapshot.markup or snapshot.text
            if raw:
                payloads.append(self._unwrap_detail_payload(platform, raw))
        return payloads

    def _unwrap_detail_payload(self, platform: str, payload: str) -> str:
        if platform == "Taobao/Tmall":
            return self.tmall_taobao.unwrap_desc_payload(payload) or payload
        return payload
