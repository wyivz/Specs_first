from __future__ import annotations

from dataclasses import dataclass, field

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
from collectors.browser import BrowserAuthRequired, PlaywrightCapture
from collectors.http import HttpClient, SearchResult, clip, extract_title, html_to_text
from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate


@dataclass
class CollectorDiagnostics:
    errors: list[str] = field(default_factory=list)

    def record(self, source: str, message: str) -> None:
        self.errors.append(f"{source}: {message}")


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

    def __init__(self, http: HttpClient, diagnostics: CollectorDiagnostics | None = None) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()

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

    def collect_specs(self, candidate: ProductCandidate) -> tuple[list[OfficialSpec], list[str]]:
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
            result = self.http.fetch(url)
            if not result.ok:
                self.diagnostics.record("official", f"failed to fetch {url}: {result.error}")
                continue
            text = html_to_text(result.text)
            page_texts.append(text)
            for spec in extract_specs_from_text(text, result.url):
                specs_by_name.setdefault(spec.name, spec)
            title = extract_title(result.text)
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
    def __init__(self, http: HttpClient, diagnostics: CollectorDiagnostics | None = None) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()

    def collect(self, candidate: ProductCandidate) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for platform, query in [
            ("Bilibili", f"{candidate.sku} site:bilibili.com 评测 紫边 对焦 卡顿"),
            ("YouTube", f"{candidate.sku} site:youtube.com review chromatic aberration focus ring issue"),
        ]:
            for result in self.http.search(query, max_results=6):
                search_evidence = evidence_from_search_result(platform, result, confidence=0.52)
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.http.fetch(result.url)
                if page.ok:
                    evidence.extend(evidence_from_page(platform, page.url, page.text, confidence=0.58))
                else:
                    self.diagnostics.record(platform, f"failed to fetch {result.url}: {page.error}")
        return dedupe_evidence(evidence)


class ForumSourceCollector:
    def __init__(self, http: HttpClient, diagnostics: CollectorDiagnostics | None = None) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()

    def collect(self, candidate: ProductCandidate) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for platform, query in [
            ("Chiphell", f"{candidate.sku} site:chiphell.com 色散 阻尼 品控 翻车"),
            ("Reddit", f"{candidate.sku} site:reddit.com chromatic aberration focus ring copy variation"),
        ]:
            for result in self.http.search(query, max_results=8):
                search_evidence = evidence_from_search_result(platform, result, confidence=0.57)
                if search_evidence:
                    evidence.append(search_evidence)
                page = self.http.fetch(result.url)
                if page.ok:
                    evidence.extend(evidence_from_page(platform, page.url, page.text, confidence=0.64))
                else:
                    self.diagnostics.record(platform, f"failed to fetch {result.url}: {page.error}")
        return dedupe_evidence(evidence)


class EcommerceSourceCollector:
    def __init__(
        self,
        http: HttpClient,
        diagnostics: CollectorDiagnostics | None = None,
        browser: PlaywrightCapture | None = None,
    ) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()
        self.browser = browser or PlaywrightCapture()

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
                combined_text = f"{result.title}. {result.snippet}"
                screenshot_path = ""
                page_ok = False
                if use_browser and result.url.startswith("http"):
                    try:
                        from pathlib import Path

                        capture = self.browser.capture_page_slices(
                            result.url,
                            task_id=task_id or "manual",
                            storage_state_path=Path(storage_state_path) if storage_state_path else None,
                        )
                        combined_text = f"{combined_text} {capture.page_text}"
                        screenshot_path = str(capture.screenshot_paths[0]) if capture.screenshot_paths else ""
                        page_ok = True
                    except BrowserAuthRequired:
                        raise
                    except Exception as exc:
                        self.diagnostics.record(platform, f"browser capture failed for {result.url}: {exc}")
                page = self.http.fetch(result.url)
                if page.ok:
                    combined_text = f"{combined_text} {html_to_text(page.text)}"
                    page_ok = True
                elif page.error:
                    self.diagnostics.record(platform, f"failed to fetch {result.url}: {page.error}")
                parsed = extract_price(combined_text)
                if not parsed:
                    continue
                evidence = build_evidence(
                    platform=platform_from_url(result.url) or platform,
                    url=result.url,
                    author=platform,
                    locator="price-text",
                    excerpt=clip(combined_text, 360),
                    confidence=0.55 if page.ok else 0.42,
                )
                findings.append(
                    PriceFinding(
                        platform=platform,
                        list_price=parsed.list_price,
                        coupon_discount=parsed.coupon_discount,
                        subsidy_discount=parsed.subsidy_discount,
                        cross_store_discount=parsed.cross_store_discount,
                        final_price=parsed.final_price,
                        screenshot_path=screenshot_path,
                        captured_at=evidence.captured_at,
                        evidence=evidence,
                    )
                )
        return sorted(findings, key=lambda item: item.final_price)[:5]


class UrlInjectionCollector:
    def __init__(self, http: HttpClient, diagnostics: CollectorDiagnostics | None = None) -> None:
        self.http = http
        self.diagnostics = diagnostics or CollectorDiagnostics()

    def collect_evidence(self, urls: list[str]) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for url in urls:
            page = self.http.fetch(url)
            if not page.ok:
                self.diagnostics.record("url", f"failed to fetch {url}: {page.error}")
                continue
            evidence.extend(evidence_from_page(platform_from_url(page.url), page.url, page.text, confidence=0.68))
        return dedupe_evidence(evidence)

    def collect_prices(self, urls: list[str]) -> list[PriceFinding]:
        prices: list[PriceFinding] = []
        for url in urls:
            page = self.http.fetch(url)
            if not page.ok:
                continue
            parsed = extract_price(html_to_text(page.text))
            if not parsed:
                continue
            evidence = build_evidence(
                platform=platform_from_url(page.url),
                url=page.url,
                author=platform_from_url(page.url),
                locator="injected-url-price",
                excerpt=clip(html_to_text(page.text), 360),
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
