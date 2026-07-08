from __future__ import annotations

import re

from collectors.extractors import build_evidence, evidence_from_page
from collectors.http import clip, html_to_text
from schemas import EvidenceItem
from schemas.category_profile import real_world_issue_patterns, review_content_patterns


class BilibiliAdapter:
    COMMENT_HINTS = re.compile("|".join(real_world_issue_patterns() + review_content_patterns()), re.I)

    def supports(self, url: str) -> bool:
        return "bilibili.com" in url.lower()

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
