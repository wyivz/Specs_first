from __future__ import annotations

import re

from collectors.adapters.bilibili_api_client import BilibiliApiClient
from collectors.credentials import BilibiliCredentials, load_bilibili_credentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import build_evidence, evidence_from_page
from collectors.http import clip, html_to_text
from collectors.platform_auth import PlatformAuthRequired
from collectors.rate_limit import PlatformRateLimiter, get_rate_limiter
from schemas import EvidenceItem
from schemas.category_profile import real_world_issue_patterns, review_content_patterns


class BilibiliAdapter:
    COMMENT_HINTS = re.compile("|".join(real_world_issue_patterns() + review_content_patterns()), re.I)

    def __init__(
        self,
        *,
        credentials: BilibiliCredentials | None = None,
        rate_limiter: PlatformRateLimiter | None = None,
        diagnostics: CollectorDiagnostics | None = None,
        max_videos_per_sku: int | None = None,
    ) -> None:
        from backend.config import settings

        self.credentials = credentials or load_bilibili_credentials()
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self.diagnostics = diagnostics
        self.max_videos_per_sku = max_videos_per_sku or settings.bilibili_max_videos_per_sku
        self._api_client: BilibiliApiClient | None = None
        if self.credentials.configured:
            self._api_client = BilibiliApiClient(
                credentials=self.credentials,
                rate_limiter=self.rate_limiter,
                diagnostics=diagnostics,
                comment_page_delay_seconds=settings.bilibili_comment_page_delay_seconds,
                max_comments_per_video=settings.bilibili_max_comments_per_video,
            )
        self._api_video_budget = self.max_videos_per_sku
        self._logged_missing_credentials = False

    def supports(self, url: str) -> bool:
        return "bilibili.com" in url.lower()

    def reset_api_budget(self) -> None:
        self._api_video_budget = self.max_videos_per_sku
        self._logged_missing_credentials = False

    def extract_evidence(self, url: str, markup: str, confidence: float = 0.62) -> list[EvidenceItem]:
        if not self.supports(url):
            return []
        evidence = evidence_from_page("Bilibili", url, markup, confidence=confidence)
        text = html_to_text(markup)
        snippets = self._extract_comment_snippets(text)
        for index, snippet in enumerate(snippets[:6]):
            if any(existing.excerpt[:80] == snippet[:80] for existing in evidence):
                continue
            evidence.append(
                build_evidence(
                    platform="Bilibili",
                    url=url,
                    author="bilibili_comment",
                    locator=f"comment-snippet-{index + 1}",
                    excerpt=snippet,
                    confidence=max(0.5, confidence - 0.05),
                )
            )

        if self._api_client and self._api_video_budget > 0 and BilibiliApiClient.extract_bvid(url):
            self._api_video_budget -= 1
            try:
                evidence.extend(self._api_client.collect_api_evidence(url, confidence=confidence + 0.04))
            except PlatformAuthRequired:
                raise
            except Exception as exc:
                if self.diagnostics:
                    self.diagnostics.record(
                        "bilibili",
                        f"API enrichment failed for {url}: {exc}; using HTML-only evidence",
                        level="warning",
                    )
        elif self._api_client is None and self.diagnostics and not self._logged_missing_credentials:
            self._logged_missing_credentials = True
            self.diagnostics.record(
                "bilibili",
                "Bilibili cookies not configured; using HTML-only extraction for subtitles/comments",
                level="info",
            )
        return evidence

    def _extract_comment_snippets(self, text: str) -> list[str]:
        snippets: list[str] = []
        for pattern in real_world_issue_patterns():
            for match in re.finditer(rf"[^。！？!?]{{12,220}}{pattern}[^。！？!?]{{0,120}}", text, re.I):
                snippet = clip(match.group(0), 360)
                if self.COMMENT_HINTS.search(snippet):
                    snippets.append(snippet)
        if snippets:
            return snippets
        for sentence in re.split(r"[。！？!?]\s*", text):
            sentence = clip(sentence, 360)
            if len(sentence) >= 16 and self.COMMENT_HINTS.search(sentence):
                snippets.append(sentence)
        return snippets[:8]
