from __future__ import annotations

import re
from dataclasses import dataclass

from collectors.adapters.bilibili_guard import is_blocked_bvid, is_rickroll_title
from collectors.credentials import BilibiliCredentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import clip
from collectors.platform_auth import PlatformAuthRequired, is_verification_error
from collectors.rate_limit import PlatformRateLimiter, get_rate_limiter
from schemas import EvidenceItem
from schemas.category_profile import real_world_issue_patterns, review_content_patterns

BVID_PATTERN = re.compile(r"(BV[A-Za-z0-9]{10})")


@dataclass
class BilibiliApiClient:
    credentials: BilibiliCredentials
    rate_limiter: PlatformRateLimiter | None = None
    diagnostics: CollectorDiagnostics | None = None
    comment_page_delay_seconds: float = 3.0
    max_comments_per_video: int = 50

    def __post_init__(self) -> None:
        self.rate_limiter = self.rate_limiter or get_rate_limiter()

    @staticmethod
    def extract_bvid(url: str) -> str:
        match = BVID_PATTERN.search(url)
        return match.group(1) if match else ""

    def fetch_subtitle_text(self, bvid: str) -> str:
        """Fetch CC subtitle text via bilibili-api-python.

        bilibili-api v17+ requires an explicit cid (分P ID).  We get it from
        get_pages() which returns a list of page dicts each with a 'cid' key.
        The subtitle metadata returned by get_subtitle(cid=cid) only contains
        a URL; we then fetch that URL to obtain the body[] array.

        If the video has no native CC subtitle, and a local ASR backend is
        installed, falls back to downloading the audio track and
        transcribing it (see ``_fetch_subtitle_via_asr``).
        """
        if not bvid or not self.credentials.configured:
            return ""
        try:
            from bilibili_api import sync, video

            credential = self.credentials.to_credential()
            video_obj = video.Video(bvid=bvid, credential=credential)

            self.rate_limiter.wait("bilibili")  # type: ignore[union-attr]
            pages = sync.sync(video_obj.get_pages())
            if not pages:
                return self._fetch_subtitle_via_asr(bvid)
            cid = pages[0].get("cid")
            if not cid:
                return self._fetch_subtitle_via_asr(bvid)

            self.rate_limiter.wait("bilibili")  # type: ignore[union-attr]
            subtitle_meta = sync.sync(video_obj.get_subtitle(cid=cid))
        except Exception as exc:
            self._record("bilibili", f"subtitle API failed for {bvid}: {exc}", level="warning")
            if is_verification_error(str(exc)):
                raise PlatformAuthRequired(
                    platform="bilibili",
                    message=f"Bilibili verification required while fetching subtitles for {bvid}",
                ) from exc
            return self._fetch_subtitle_via_asr(bvid)

        subtitles = (subtitle_meta or {}).get("subtitles") or []
        if not subtitles:
            return self._fetch_subtitle_via_asr(bvid)

        subtitle_url = subtitles[0].get("subtitle_url") or ""
        if not subtitle_url:
            return self._fetch_subtitle_via_asr(bvid)
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url

        try:
            import json as _json
            import urllib.request as _req
            req = _req.Request(subtitle_url, headers={"User-Agent": "Mozilla/5.0"})
            with _req.urlopen(req, timeout=10) as resp:
                body_data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            self._record("bilibili", f"subtitle body fetch failed for {bvid}: {exc}", level="warning")
            return self._fetch_subtitle_via_asr(bvid)

        body = body_data.get("body") or []
        parts = [item.get("content", "").strip() for item in body if item.get("content")]
        text = clip(" ".join(parts), 12000)
        return text or self._fetch_subtitle_via_asr(bvid)

    def _fetch_subtitle_via_asr(self, bvid: str) -> str:
        """Download audio + run local ASR when no native CC subtitle exists.

        No-op (returns "") unless BILIBILI_ASR_FALLBACK is enabled and a
        local ASR backend (funasr / faster-whisper) plus yt-dlp are
        installed. Never raises: any failure just yields an empty transcript
        so the caller degrades to comment-only evidence.
        """
        from collectors.settings import settings

        if not settings.bilibili_asr_fallback:
            return ""
        try:
            from collectors.asr import available_backend, transcribe_url
        except Exception:
            return ""
        if available_backend() is None:
            self._record(
                "bilibili",
                f"no native subtitle for {bvid} and no local ASR backend installed; skipping transcript",
                level="info",
            )
            return ""

        url = f"https://www.bilibili.com/video/{bvid}"
        self._record("bilibili", f"no native subtitle for {bvid}; falling back to local ASR", level="info")
        try:
            result = transcribe_url(url, language="zh")
        except Exception as exc:
            self._record("bilibili", f"ASR fallback failed for {bvid}: {exc}", level="warning")
            return ""
        if not result.ok:
            if result.error:
                self._record("bilibili", f"ASR fallback failed for {bvid}: {result.error}", level="warning")
            return ""
        return clip(result.text, 12000)

    def fetch_comment_texts(self, bvid: str) -> list[str]:
        if not bvid or not self.credentials.configured:
            return []
        try:
            from bilibili_api import comment, sync, video
            from bilibili_api.comment import CommentResourceType, OrderType

            credential = self.credentials.to_credential()
            video_obj = video.Video(bvid=bvid, credential=credential)
            self.rate_limiter.wait("bilibili")  # type: ignore[union-attr]
            info = sync.sync(video_obj.get_info())
            oid = int(info.get("aid") or 0)
            if not oid:
                return []

            comments: list[str] = []
            page_index = 1
            while len(comments) < self.max_comments_per_video:
                self.rate_limiter.wait("bilibili")  # type: ignore[union-attr]
                try:
                    page = sync.sync(
                        comment.get_comments(
                            oid,
                            CommentResourceType.VIDEO,
                            page_index=page_index,
                            order=OrderType.LIKE,
                            credential=credential,
                        )
                    )
                except Exception as exc:
                    if is_verification_error(str(exc)):
                        raise PlatformAuthRequired(
                            platform="bilibili",
                            message=f"Bilibili verification required while fetching comments for {bvid}",
                        ) from exc
                    self._record("bilibili", f"comment page {page_index} failed for {bvid}: {exc}")
                    break

                replies = page.get("replies") or []
                if not replies:
                    break
                for reply in replies:
                    content = ((reply.get("content") or {}).get("message") or "").strip()
                    if content:
                        comments.append(content)
                    if len(comments) >= self.max_comments_per_video:
                        break
                if not page.get("page", {}).get("count") or page_index * 20 >= page["page"]["count"]:
                    break
                page_index += 1
            return comments
        except PlatformAuthRequired:
            raise
        except Exception as exc:
            self._record("bilibili", f"comment API failed for {bvid}: {exc}")
            if is_verification_error(str(exc)):
                raise PlatformAuthRequired(
                    platform="bilibili",
                    message=f"Bilibili verification required while fetching comments for {bvid}",
                ) from exc
            return []

    def fetch_video_title(self, bvid: str) -> str:
        if not bvid or not self.credentials.configured:
            return ""
        try:
            from bilibili_api import sync, video

            credential = self.credentials.to_credential()
            video_obj = video.Video(bvid=bvid, credential=credential)
            self.rate_limiter.wait("bilibili")  # type: ignore[union-attr]
            info = sync.sync(video_obj.get_info())
            return str(info.get("title") or "").strip()
        except Exception as exc:
            self._record("bilibili", f"title API failed for {bvid}: {exc}", level="warning")
            return ""

    def collect_api_evidence(self, url: str, *, confidence: float = 0.68) -> list[EvidenceItem]:
        from collectors.extractors import build_evidence

        bvid = self.extract_bvid(url)
        if not bvid:
            return []
        if is_blocked_bvid(bvid):
            self._record(
                "bilibili",
                f"Blocked BVID {bvid}: known non-product placeholder (paste a real product review BV)",
                level="warning",
            )
            return []

        evidence: list[EvidenceItem] = []
        title = self.fetch_video_title(bvid)
        if title and is_rickroll_title(title):
            self._record(
                "bilibili",
                f"BVID {bvid} title looks like meme/rickroll ({title[:80]}); skipping API evidence",
                level="warning",
            )
            return []
        subtitle = self.fetch_subtitle_text(bvid)
        comment_texts = self.fetch_comment_texts(bvid)
        self._record(
            "bilibili",
            f"API fetch bvid={bvid} title={title[:80] if title else '-'} subtitle_len={len(subtitle)} comments={len(comment_texts)}",
            level="info",
        )
        if subtitle:
            for index, snippet in enumerate(self._review_snippets(subtitle)[:8]):
                evidence.append(
                    build_evidence(
                        platform="Bilibili",
                        url=url,
                        author="bilibili_subtitle",
                        locator=f"subtitle-snippet-{index + 1}",
                        excerpt=snippet,
                        confidence=max(0.6, confidence),
                    )
                )

        for index, text in enumerate(self._select_comment_snippets(comment_texts)[:8]):
            evidence.append(
                build_evidence(
                    platform="Bilibili",
                    url=url,
                    author="bilibili_comment",
                    locator=f"api-comment-{index + 1}",
                    excerpt=clip(text, 360),
                    confidence=max(0.55, confidence - 0.05),
                )
            )
        return evidence

    def _select_comment_snippets(self, comments: list[str]) -> list[str]:
        hints = re.compile("|".join(real_world_issue_patterns() + review_content_patterns()), re.I)
        matched = [comment for comment in comments if hints.search(comment)]
        return matched or comments

    def _review_snippets(self, text: str) -> list[str]:
        hints = re.compile("|".join(real_world_issue_patterns() + review_content_patterns()), re.I)
        snippets: list[str] = []
        for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
            sentence = clip(sentence.strip(), 360)
            if len(sentence) >= 16 and hints.search(sentence):
                snippets.append(sentence)
        if snippets:
            return snippets
        return [clip(text, 360)] if text else []

    def _record(self, source: str, message: str, *, level: str = "warning") -> None:
        if self.diagnostics:
            self.diagnostics.record(source, message, level=level)
