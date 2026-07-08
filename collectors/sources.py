from __future__ import annotations

from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.youtube import YouTubeAdapter
from collectors.adapters.youtube_comments import YouTubeCommentFetcher
from collectors.adapters.jd import JdAdapter
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.credentials import load_taobao_credentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    build_evidence,
    candidate_from_search_result,
    dedupe_evidence,
    extract_desc_api_urls,
    extract_detail_image_urls,
    evidence_from_page,
    evidence_from_search_result,
    extract_price,
    extract_specs_from_markup,
    extract_specs_from_text,
    infer_specs_from_sku,
    platform_from_url,
)
from collectors.http import FetchResult, HttpClient, clip, extract_title
from collectors.page_sanitize import sanitize_html
from collectors.platform_auth import PlatformAuthRequired
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate
from schemas.category_profile import (
    ecommerce_search_queries,
    forum_search_queries,
    video_search_queries,
)


def _rich_text(markup: str, url: str) -> str:
    return sanitize_html(url, markup).rich_text


class OfficialSourceCollector:
    OFFICIAL_HINTS = [
        "official",
        "specifications",
        "manual",
        "white paper",
        "datasheet",
        "官网",
        "规格",
        "说明书",
        "白皮书",
    ]

    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)

    def discover_candidates(self, query: str, category: str, max_results: int = 10) -> list[ProductCandidate]:
        search_query = f"{query} {category} official specifications"
        results = self.http.search(search_query, max_results=max_results * 2)
        candidates: list[ProductCandidate] = []
        for result in results:
            if self._looks_relevant(result):
                candidates.append(candidate_from_search_result(result, category))
        if not candidates:
            candidates = [
                ProductCandidate(
                    sku=query.strip() or "Unknown Product",
                    brand=query.split()[0] if query.split() else "Unknown",
                    category=category,
                    source_url=results[0].url if results else "https://example.invalid/no-source",
                    confidence=0.35,
                )
            ]
        return candidates[:max_results]

    def collect_specs(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        urls = [candidate.source_url]
        urls.extend(
            result.url
            for result in self.http.search(f"{candidate.sku} official specifications manual", max_results=5)
            if self._looks_relevant(result)
        )

        specs_by_name: dict[str, OfficialSpec] = {}
        highlights: list[str] = []
        page_texts: list[str] = []
        for url in dict.fromkeys(urls):
            if not url.startswith("http"):
                continue
            snapshot = self.resilient.fetch(
                url,
                task_id=task_id,
                use_browser=use_browser,
                storage_state_path=storage_state_path,
                sku=candidate.sku,
            )
            if not snapshot.ok:
                self.diagnostics.record(
                    "official",
                    f"weak page snapshot for {url}: {snapshot.error or snapshot.page.blockers}",
                    level="warning",
                    sku=candidate.sku,
                )
                if not snapshot.markup:
                    continue
            text = snapshot.text
            page_texts.append(text)
            for spec in extract_specs_from_text(text, snapshot.url, candidate.category):
                specs_by_name.setdefault(spec.name, spec)
            title = snapshot.page.title or extract_title(snapshot.markup)
            if title and len(highlights) < 3:
                highlights.append(clip(title, 80))

        combined_text = "\n\n".join(page_texts)
        if combined_text.strip():
            try:
                from backend.model_router import create_model_router

                router = create_model_router()
                gemini_specs, gemini_highlights = router.extract_official_specs_from_text(
                    candidate.sku,
                    combined_text,
                    candidate.source_url,
                    category=candidate.category,
                )
                for spec in gemini_specs:
                    specs_by_name.setdefault(spec.name, spec)
                for item in gemini_highlights:
                    if item not in highlights and len(highlights) < 5:
                        highlights.append(item)
            except Exception:
                pass

        for spec in infer_specs_from_sku(candidate):
            specs_by_name.setdefault(spec.name, spec)
        return list(specs_by_name.values()), highlights

    def _looks_relevant(self, result: SearchResult) -> bool:
        combined = f"{result.title} {result.snippet}".lower()
        return any(hint in combined for hint in self.OFFICIAL_HINTS) or result.url.startswith("http")


class VideoSourceCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
    ) -> None:
        from backend.config import settings

        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)
        self.bilibili = BilibiliAdapter(diagnostics=self.diagnostics)
        self.youtube = YouTubeAdapter(
            http,
            diagnostics=self.diagnostics,
            comment_fetcher=YouTubeCommentFetcher(
                max_comments_per_video=settings.youtube_comment_max_per_video,
                delay_min_seconds=settings.youtube_comment_delay_min,
                delay_max_seconds=settings.youtube_comment_delay_max,
                diagnostics=self.diagnostics,
            ),
        )

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        self.bilibili.reset_api_budget()
        for platform, query in video_search_queries(candidate.sku):
            for result in self.http.search(query, max_results=6):
                search_evidence = evidence_from_search_result(platform, result, confidence=0.52)
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.resilient.fetch(
                    result.url,
                    task_id=task_id,
                    use_browser=use_browser or platform in {"Bilibili", "YouTube"},
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                if page.ok or page.markup:
                    try:
                        if platform == "Bilibili" and self.bilibili.supports(page.url):
                            evidence.extend(
                                self.bilibili.extract_evidence(page.url, page.markup, confidence=0.6)
                            )
                        elif platform == "YouTube" and self.youtube.supports(page.url):
                            evidence.extend(
                                self.youtube.extract_evidence(page.url, page.markup, confidence=0.62)
                            )
                        else:
                            evidence.extend(evidence_from_page(platform, page.url, page.markup, confidence=0.58))
                    except PlatformAuthRequired as exc:
                        exc.url = exc.url or page.url
                        raise
                else:
                    self.diagnostics.record(
                        platform,
                        f"failed to fetch {result.url}: {page.error or page.page.blockers}",
                        sku=candidate.sku,
                    )
        return dedupe_evidence(evidence)


class ForumSourceCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        resilient: ResilientFetcher | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for platform, query in forum_search_queries(candidate.sku):
            for result in self.http.search(query, max_results=8):
                search_evidence = evidence_from_search_result(platform, result, confidence=0.57)
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.resilient.fetch(
                    result.url,
                    task_id=task_id,
                    use_browser=use_browser or "chiphell.com" in result.url.lower(),
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                if page.ok or page.markup:
                    evidence.extend(evidence_from_page(platform, page.url, page.markup, confidence=0.64))
                else:
                    self.diagnostics.record(
                        platform,
                        f"failed to fetch {result.url}: {page.error or page.page.blockers}",
                        sku=candidate.sku,
                    )
        return dedupe_evidence(evidence)


class EcommerceSourceCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        browser: PlaywrightCapture | None = None,
        resilient: ResilientFetcher | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.browser = browser or PlaywrightCapture()
        self.resilient = resilient or ResilientFetcher(http, self.browser, self.diagnostics)
        self.jd = JdAdapter()
        self.tmall_taobao = TmallTaobaoAdapter(load_taobao_credentials())

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        findings: list[PriceFinding] = []
        for platform, query in ecommerce_search_queries(candidate.sku):
            for result in self.http.search(query, max_results=5):
                if platform == "JD":
                    target_url = self.jd.normalize_url(result.url)
                elif platform == "Taobao/Tmall":
                    target_url = self.tmall_taobao.normalize_url(result.url)
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
                    jd_finding = self.jd.build_price_finding(snapshot.url, snapshot.markup, platform="JD")
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
                    self.diagnostics.record(
                        platform,
                        f"no price parsed for {target_url}",
                        level="info",
                        sku=candidate.sku,
                    )
                    continue
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
        from backend.model_router import create_model_router

        router = create_model_router()
        specs_by_name: dict[str, OfficialSpec] = {}
        highlights: list[str] = []
        for platform, query in ecommerce_search_queries(candidate.sku):
            for result in self.http.search(query, max_results=4):
                if platform == "JD":
                    target_url = self.jd.normalize_url(result.url)
                elif platform == "Taobao/Tmall":
                    target_url = self.tmall_taobao.normalize_url(result.url)
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
                image_specs, image_highlights = router.extract_official_specs_from_images(
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
