from __future__ import annotations

from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.jd import JdAdapter
from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import (
    build_evidence,
    candidate_from_search_result,
    dedupe_evidence,
    evidence_from_page,
    evidence_from_search_result,
    extract_price,
    extract_specs_from_text,
    infer_specs_from_sku,
    platform_from_url,
)
from collectors.http import HttpClient, SearchResult, clip, extract_title
from collectors.page_sanitize import sanitize_html
from collectors.resilient_fetch import ResilientFetcher
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate


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
            for spec in extract_specs_from_text(text, snapshot.url):
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
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.resilient = resilient or ResilientFetcher(http, diagnostics=self.diagnostics)
        self.bilibili = BilibiliAdapter()

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for platform, query in [
            ("Bilibili", f"{candidate.sku} site:bilibili.com 评测 紫边 对焦 卡顿"),
            ("YouTube", f"{candidate.sku} site:youtube.com review chromatic aberration focus ring issue"),
        ]:
            for result in self.http.search(query, max_results=6):
                search_evidence = evidence_from_search_result(platform, result, confidence=0.52)
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.resilient.fetch(
                    result.url,
                    task_id=task_id,
                    use_browser=use_browser or platform == "Bilibili",
                    storage_state_path=storage_state_path,
                    sku=candidate.sku,
                )
                if page.ok or page.markup:
                    if platform == "Bilibili" and self.bilibili.supports(page.url):
                        evidence.extend(
                            self.bilibili.extract_evidence(page.url, page.markup, confidence=0.6)
                        )
                    else:
                        evidence.extend(evidence_from_page(platform, page.url, page.markup, confidence=0.58))
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
        for platform, query in [
            ("Chiphell", f"{candidate.sku} site:chiphell.com 色散 阻尼 品控 翻车"),
            ("Reddit", f"{candidate.sku} site:reddit.com chromatic aberration focus ring copy variation"),
        ]:
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

    def collect(
        self,
        candidate: ProductCandidate,
        *,
        task_id: str = "",
        use_browser: bool = False,
        storage_state_path: str = "",
    ) -> list[PriceFinding]:
        findings: list[PriceFinding] = []
        for platform, query in [
            ("JD", f"{candidate.sku} site:jd.com 到手价 优惠券 百亿补贴"),
            ("Taobao/Tmall", f"{candidate.sku} site:taobao.com OR site:tmall.com 到手价 券后"),
        ]:
            for result in self.http.search(query, max_results=5):
                target_url = self.jd.normalize_url(result.url) if platform == "JD" else result.url
                combined_text = f"{result.title}. {result.snippet}"
                try:
                    snapshot = self.resilient.fetch(
                        target_url,
                        task_id=task_id,
                        use_browser=use_browser or platform in {"JD", "Taobao/Tmall"},
                        storage_state_path=storage_state_path,
                        sku=candidate.sku,
                    )
                except BrowserAuthRequired:
                    raise
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
