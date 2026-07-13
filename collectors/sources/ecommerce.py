from __future__ import annotations

from collectors.adapters.jd import JdAdapter
from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.collection_trace import CollectionTrace, create_collection_trace
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    build_evidence,
    evidence_mentions_sku,
    extract_desc_api_urls,
    extract_detail_image_urls,
    extract_price,
    extract_specs_from_markup,
    page_matches_sku,
    platform_from_url,
    primary_model_code,
)
from collectors.http import HttpClient, clip
from collectors.platform_auth import PlatformAuthRequired
from collectors.protocols import SpecExtractionRouter
from collectors.resilient_fetch import ResilientFetcher
from collectors.url_guards import is_noisy_ecommerce_url
from schemas import OfficialSpec, PriceFinding, ProductCandidate
from schemas.category_profile import DynamicCategoryProfile, ecommerce_search_queries


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
        self.category_profile: DynamicCategoryProfile | None = None
        self.jd = self.registry.require(JdAdapter)
        self.tmall_taobao = self.registry.require(TmallTaobaoAdapter)

    def _search_modifiers(self) -> list[str] | None:
        if self.category_profile and self.category_profile.search_modifiers:
            return list(self.category_profile.search_modifiers)
        return None

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
        for platform, query in ecommerce_search_queries(candidate.sku, modifiers=self._search_modifiers()):
            if active_trace:
                active_trace.log("ecommerce", f"search platform={platform} query={query}", sku=candidate.sku)
            for result in self.http.search(query, max_results=5):
                adapter = self.registry.for_platform(platform)
                if adapter is not None and hasattr(adapter, "normalize_url"):
                    target_url = adapter.normalize_url(result.url)
                else:
                    target_url = result.url
                if not self._is_product_result(platform, target_url, result.url):
                    self.diagnostics.record(
                        platform,
                        f"skip non-product ecommerce url: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                if not evidence_mentions_sku(candidate.sku, result.title, result.snippet, result.url):
                    self.diagnostics.record(
                        platform,
                        f"skip unrelated ecommerce search hit: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                combined_text = f"{result.title}. {result.snippet}"
                fetch_browser = use_browser
                if platform == "Taobao/Tmall" and not (self.tmall_taobao.credentials.cookie or "").strip():
                    # Without Cookie, headed captcha windows are useless noise — stay HTTP-only.
                    fetch_browser = False
                try:
                    snapshot = self.resilient.fetch(
                        target_url,
                        task_id=task_id,
                        use_browser=fetch_browser,
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                except Exception as exc:
                    if not self._soft_skip_auth(platform, target_url, exc, candidate.sku):
                        raise
                    continue
                if not self._final_url_is_product(platform, target_url, snapshot.url):
                    self.diagnostics.record(
                        platform,
                        f"skip redirected non-product page: {target_url} -> {snapshot.url}",
                        level="warning",
                        sku=candidate.sku,
                    )
                    # JD HTML often redirects home without Cookie; mgets can still price by sku id.
                    if platform == "JD" and self.jd.is_product_url(target_url):
                        jd_finding = self.jd.build_price_finding(
                            target_url,
                            "",
                            platform="JD",
                            http=self.http,
                            trace=active_trace,
                            sku=candidate.sku,
                        )
                        if jd_finding:
                            findings.append(jd_finding)
                    continue
                if not self._page_matches_target(candidate.sku, result.title, snapshot):
                    self.diagnostics.record(
                        platform,
                        f"skip ecommerce page that does not match target sku: {snapshot.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                try:
                    if platform == "Taobao/Tmall":
                        self.tmall_taobao.maybe_raise_page_auth(
                            snapshot.text,
                            snapshot.page.blockers,
                            snapshot.url,
                        )
                except PlatformAuthRequired as exc:
                    if not self._soft_skip_auth(platform, snapshot.url or target_url, exc, candidate.sku):
                        raise
                    continue
                screenshot_paths = list(snapshot.screenshot_paths)
                combined_text = f"{combined_text} {snapshot.text}"
                if platform == "JD" and snapshot.markup:
                    jd_finding = self.jd.build_price_finding(
                        target_url,
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
        for platform, query in ecommerce_search_queries(candidate.sku, modifiers=self._search_modifiers()):
            for result in self.http.search(query, max_results=4):
                adapter = self.registry.for_platform(platform)
                if adapter is not None and hasattr(adapter, "normalize_url"):
                    target_url = adapter.normalize_url(result.url)
                else:
                    target_url = result.url
                if not self._is_product_result(platform, target_url, result.url):
                    self.diagnostics.record(
                        platform,
                        f"skip non-product ecommerce url: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                if not evidence_mentions_sku(candidate.sku, result.title, result.snippet, result.url):
                    self.diagnostics.record(
                        platform,
                        f"skip unrelated ecommerce search hit: {result.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                fetch_browser = use_browser if platform != "JD" else False
                if platform == "Taobao/Tmall" and not (self.tmall_taobao.credentials.cookie or "").strip():
                    fetch_browser = False
                try:
                    snapshot = self.resilient.fetch(
                        target_url,
                        task_id=task_id,
                        # JD prefers HTTP(+Cookie)/mgets; only escalate when page is weak.
                        use_browser=fetch_browser,
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                except Exception as exc:
                    if not self._soft_skip_auth(platform, target_url, exc, candidate.sku):
                        raise
                    continue
                if not self._final_url_is_product(platform, target_url, snapshot.url):
                    self.diagnostics.record(
                        platform,
                        f"skip redirected non-product page: {target_url} -> {snapshot.url}",
                        level="warning",
                        sku=candidate.sku,
                    )
                    continue
                if not self._page_matches_target(candidate.sku, result.title, snapshot):
                    self.diagnostics.record(
                        platform,
                        f"skip ecommerce page that does not match target sku: {snapshot.url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
                try:
                    if platform == "Taobao/Tmall":
                        self.tmall_taobao.maybe_raise_page_auth(
                            snapshot.text,
                            snapshot.page.blockers,
                            snapshot.url,
                        )
                except PlatformAuthRequired as exc:
                    if not self._soft_skip_auth(platform, snapshot.url or target_url, exc, candidate.sku):
                        raise
                    continue
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
                extracted = extract_specs_from_markup(
                    merged_markup,
                    snapshot.url,
                    candidate.category,
                    profile=self.category_profile,
                )
                for spec in extracted:
                    specs_by_name.setdefault(spec.name, spec)
                detail_images = extract_detail_image_urls(merged_markup)
                if self.router is not None and use_browser:
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
                # Capture metadata belongs in diagnostics, not product highlights.
                if detail_images:
                    self.diagnostics.record(
                        platform,
                        f"detail images captured: {len(detail_images)}",
                        level="info",
                        sku=candidate.sku,
                    )
                if specs_by_name:
                    self.diagnostics.record(
                        platform,
                        "parameter block captured",
                        level="info",
                        sku=candidate.sku,
                    )
        return list(specs_by_name.values()), highlights

    def probe_detail_images(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
        max_images: int = 8,
    ) -> list[str]:
        """Light probe: return detail image URLs without filling spec slots."""
        images: list[str] = []
        seen: set[str] = set()
        for platform, query in ecommerce_search_queries(candidate.sku, modifiers=self._search_modifiers()):
            for result in self.http.search(query, max_results=3):
                adapter = self.registry.for_platform(platform)
                if adapter is not None and hasattr(adapter, "normalize_url"):
                    target_url = adapter.normalize_url(result.url)
                else:
                    target_url = result.url
                if not self._is_product_result(platform, target_url, result.url):
                    continue
                if not evidence_mentions_sku(candidate.sku, result.title, result.snippet, result.url):
                    continue
                fetch_browser = use_browser if platform != "JD" else False
                if platform == "Taobao/Tmall" and not (self.tmall_taobao.credentials.cookie or "").strip():
                    fetch_browser = False
                try:
                    snapshot = self.resilient.fetch(
                        target_url,
                        task_id=task_id,
                        use_browser=fetch_browser,
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                except Exception as exc:
                    if not self._soft_skip_auth(platform, target_url, exc, candidate.sku):
                        raise
                    continue
                if not snapshot.markup:
                    continue
                if not self._final_url_is_product(platform, target_url, snapshot.url):
                    continue
                if not self._page_matches_target(candidate.sku, result.title, snapshot):
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
                for url in extract_detail_image_urls(merged_markup):
                    if url in seen:
                        continue
                    seen.add(url)
                    images.append(url)
                    if len(images) >= max_images:
                        self.diagnostics.record(
                            platform,
                            f"schema probe captured {len(images)} detail images",
                            level="info",
                            sku=candidate.sku,
                        )
                        return images
        if images:
            self.diagnostics.record(
                "ecommerce",
                f"schema probe captured {len(images)} detail images",
                level="info",
                sku=candidate.sku,
            )
        return images

    def _is_product_result(self, platform: str, normalized_url: str, raw_url: str = "") -> bool:
        if is_noisy_ecommerce_url(normalized_url) or is_noisy_ecommerce_url(raw_url):
            return False
        adapter = self.registry.for_platform(platform)
        if adapter is not None and hasattr(adapter, "is_product_url"):
            return bool(adapter.is_product_url(normalized_url) or adapter.is_product_url(raw_url))
        lower = f"{normalized_url} {raw_url}".lower()
        if platform == "JD":
            return "item.jd.com/" in lower or "item.m.jd.com/" in lower
        if platform == "Taobao/Tmall":
            return "item.taobao.com/" in lower or "detail.tmall.com/" in lower
        return bool(normalized_url.startswith("http"))

    def _final_url_is_product(self, platform: str, requested_url: str, final_url: str) -> bool:
        """Reject login/homepage redirects that keep the requested item URL only in history."""
        if not final_url:
            return True
        if final_url.rstrip("/") == requested_url.rstrip("/"):
            return True
        return self._is_product_result(platform, final_url, requested_url)

    def _page_matches_target(self, sku: str, search_title: str, snapshot: object) -> bool:
        """Reject listings whose on-page title clearly belongs to another product."""
        if not sku or not sku.strip():
            return True
        title = ""
        text = ""
        url = ""
        page = getattr(snapshot, "page", None)
        if page is not None:
            title = str(getattr(page, "title", "") or "")
        text = str(getattr(snapshot, "text", "") or "")
        url = str(getattr(snapshot, "url", "") or "")
        if title.strip() and len(title.strip()) >= 8 and not evidence_mentions_sku(sku, title, url):
            # A concrete alternate product title means DDG ranked a wrong item.
            if primary_model_code(title) or len(title.strip()) >= 12:
                return False
        if page_matches_sku(sku, title=title or search_title, text=text, url=url):
            return True
        return evidence_mentions_sku(sku, search_title)

    def _soft_skip_auth(self, platform: str, url: str, exc: Exception, sku: str) -> bool:
        """Return True when the caller should continue instead of pausing the pipeline.

        - PlatformAuthRequired (mtop/session): always soft-skip; JD can still succeed.
        - Taobao/Tmall BrowserAuthRequired: soft-skip (slider is flaky in CLI; update Cookie).
        - Non-product BrowserAuthRequired: soft-skip.
        - JD product BrowserAuthRequired: do NOT soft-skip (Streamlit HITL still needed).
        """
        if isinstance(exc, PlatformAuthRequired):
            self.diagnostics.record(
                platform,
                f"soft-skip platform auth for {url}: {exc}",
                level="warning",
                sku=sku,
            )
            return True
        if isinstance(exc, BrowserAuthRequired):
            pause_url = getattr(exc, "url", "") or url
            if platform == "Taobao/Tmall" or not self._is_product_result(platform, pause_url, url):
                self.diagnostics.record(
                    platform,
                    f"soft-skip browser auth for {pause_url}: {exc}",
                    level="warning",
                    sku=sku,
                )
                return True
            return False
        return False

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
                except PlatformAuthRequired as exc:
                    self._soft_skip_auth(platform, detail_url, exc, sku)
                    continue
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
            except Exception as exc:
                if not self._soft_skip_auth(platform, detail_url, exc, sku):
                    raise
                continue
            raw = snapshot.markup or snapshot.text
            if raw:
                payloads.append(self._unwrap_detail_payload(platform, raw))
        return payloads

    def _unwrap_detail_payload(self, platform: str, payload: str) -> str:
        if platform == "Taobao/Tmall":
            return self.tmall_taobao.unwrap_desc_payload(payload) or payload
        return payload
