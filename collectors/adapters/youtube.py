from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from collectors.adapters.youtube_comments import YouTubeCommentFetcher
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import build_evidence, evidence_from_page
from collectors.http import HttpClient, clip
from collectors.rate_limit import PlatformRateLimiter, get_rate_limiter
from schemas import EvidenceItem
from schemas.category_profile import real_world_issue_patterns, review_content_patterns


class YouTubeAdapter:
    REVIEW_HINTS = re.compile("|".join(real_world_issue_patterns() + review_content_patterns()), re.I)
    VIDEO_ID_PATTERN = re.compile(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{6,})")

    def __init__(
        self,
        http: HttpClient | None = None,
        *,
        comment_fetcher: YouTubeCommentFetcher | None = None,
        rate_limiter: PlatformRateLimiter | None = None,
        diagnostics: CollectorDiagnostics | None = None,
    ) -> None:
        self.http = http or HttpClient()
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self.diagnostics = diagnostics
        self.comment_fetcher = comment_fetcher or YouTubeCommentFetcher(diagnostics=diagnostics)

    def supports(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "youtube.com" in host or host.endswith("youtu.be")

    def extract_video_id(self, url: str) -> str:
        match = self.VIDEO_ID_PATTERN.search(url)
        if match:
            return match.group(1)
        parsed = urlparse(url)
        if parsed.netloc.lower().endswith("youtu.be") and parsed.path.strip("/"):
            return parsed.path.strip("/").split("/")[0]
        query = parse_qs(parsed.query).get("v", [])
        return query[0] if query else ""

    def extract_evidence(
        self,
        url: str,
        markup: str,
        *,
        confidence: float = 0.6,
    ) -> list[EvidenceItem]:
        if not self.supports(url):
            return []
        video_id = self.extract_video_id(url)
        watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else url
        evidence = evidence_from_page("YouTube", watch_url, markup, confidence=confidence - 0.08)

        transcript = self.fetch_transcript(watch_url, markup=markup, video_id=video_id)
        for index, snippet in enumerate(self._review_snippets(transcript)[:8]):
            evidence.append(
                build_evidence(
                    platform="YouTube",
                    url=watch_url,
                    author="youtube_transcript",
                    locator=f"transcript-snippet-{index + 1}",
                    excerpt=snippet,
                    confidence=max(0.55, confidence),
                )
            )

        for index, snippet in enumerate(self._extract_comment_snippets(markup)[:4]):
            if any(existing.excerpt[:80] == snippet[:80] for existing in evidence):
                continue
            evidence.append(
                build_evidence(
                    platform="YouTube",
                    url=watch_url,
                    author="youtube_comment",
                    locator=f"comment-snippet-{index + 1}",
                    excerpt=snippet,
                    confidence=max(0.5, confidence - 0.08),
                )
            )

        api_comments = self.comment_fetcher.fetch_comment_texts(watch_url, video_id=video_id)
        for index, snippet in enumerate(self.comment_fetcher.select_review_comments(api_comments)[:6]):
            if any(existing.excerpt[:80] == snippet[:80] for existing in evidence):
                continue
            evidence.append(
                build_evidence(
                    platform="YouTube",
                    url=watch_url,
                    author="youtube_comment",
                    locator=f"api-comment-{index + 1}",
                    excerpt=snippet,
                    confidence=max(0.58, confidence - 0.04),
                )
            )
        return evidence

    def fetch_transcript(
        self,
        url: str,
        *,
        markup: str = "",
        video_id: str = "",
        preferred_languages: tuple[str, ...] = ("zh", "zh-Hans", "zh-Hant", "en"),
    ) -> str:
        resolved_id = video_id or self.extract_video_id(url)
        if not resolved_id:
            return ""
        player = self._extract_embedded_json(markup, "ytInitialPlayerResponse") if markup else None
        if player is None:
            page = self.http.fetch(url)
            if not page.ok:
                return self._fetch_transcript_fallback(resolved_id, preferred_languages)
            player = self._extract_embedded_json(page.text, "ytInitialPlayerResponse")
        if not player:
            return self._fetch_transcript_fallback(resolved_id, preferred_languages)
        tracks = (
            player.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        if not tracks:
            return self._fetch_transcript_fallback(resolved_id, preferred_languages)
        track = self._pick_caption_track(tracks, preferred_languages)
        if not track:
            return ""
        caption_url = track.get("baseUrl") or ""
        if not caption_url:
            return ""
        if "fmt=" not in caption_url:
            separator = "&" if "?" in caption_url else "?"
            caption_url = f"{caption_url}{separator}fmt=srv3"
        # YouTube enforces PoToken for URLs containing &exp=xpe — the timedtext
        # endpoint returns an empty 200 body in that case.  We detect it early
        # and skip straight to the transcript-api fallback.
        if "&exp=xpe" in caption_url:
            if self.diagnostics:
                self.diagnostics.record(
                    "youtube",
                    f"captionTrack for {resolved_id} requires PoToken (&exp=xpe); "
                    "falling back to youtube-transcript-api",
                    level="info",
                )
            return self._fetch_transcript_fallback(resolved_id, preferred_languages)
        result = self.http.fetch(caption_url)
        if not result.ok:
            return self._fetch_transcript_fallback(resolved_id, preferred_languages)
        transcript = self._parse_caption_payload(result.text)
        if transcript:
            return transcript
        return self._fetch_transcript_fallback(resolved_id, preferred_languages)

    def _fetch_transcript_fallback(
        self,
        video_id: str,
        preferred_languages: tuple[str, ...],
    ) -> str:
        try:
            from youtube_transcript_api import (
                YouTubeTranscriptApi,
                PoTokenRequired,
                RequestBlocked,
                IpBlocked,
                TranscriptsDisabled,
                NoTranscriptFound,
                VideoUnavailable,
            )
        except ImportError:
            return ""
        self.rate_limiter.wait("youtube")
        try:
            languages = list(preferred_languages) + ["en"]
            fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        except PoTokenRequired:
            if self.diagnostics:
                self.diagnostics.record(
                    "youtube",
                    f"transcript-api: PoToken required for {video_id} (YouTube bot-detection); "
                    "transcript unavailable without a residential IP",
                    level="info",
                )
            return ""
        except (RequestBlocked, IpBlocked) as exc:
            if self.diagnostics:
                self.diagnostics.record(
                    "youtube",
                    f"transcript-api: IP blocked for {video_id}: {exc}",
                    level="info",
                )
            return ""
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
            return ""
        except Exception as exc:
            if self.diagnostics:
                self.diagnostics.record(
                    "youtube",
                    f"transcript-api fallback failed for {video_id}: {exc}",
                    level="info",
                )
            return ""
        parts = [item.get("text", "").strip() for item in fetched if item.get("text")]
        return clip(" ".join(parts), 12000)

    def _pick_caption_track(self, tracks: list[dict], preferred_languages: tuple[str, ...]) -> dict | None:
        for language in preferred_languages:
            for track in tracks:
                code = (track.get("languageCode") or "").lower()
                if code == language.lower() or code.startswith(language.lower()):
                    return track
        return tracks[0] if tracks else None

    def _parse_caption_payload(self, payload: str) -> str:
        payload = payload.strip()
        if not payload:
            return ""
        if payload.startswith("{"):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return ""
            events = data.get("events") or []
            parts: list[str] = []
            for event in events:
                for segment in event.get("segs") or []:
                    text = segment.get("utf8") or ""
                    if text.strip():
                        parts.append(text)
            return clip(" ".join(parts), 12000)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return clip(re.sub(r"<[^>]+>", " ", payload), 12000)
        parts = [elem.text.strip() for elem in root.iter("text") if elem.text and elem.text.strip()]
        return clip(" ".join(parts), 12000)

    def _review_snippets(self, transcript: str) -> list[str]:
        if not transcript:
            return []
        snippets: list[str] = []
        for sentence in re.split(r"(?<=[.!?。！？])\s+", transcript):
            sentence = clip(sentence.strip(), 360)
            if len(sentence) >= 20 and self.REVIEW_HINTS.search(sentence):
                snippets.append(sentence)
        if snippets:
            return snippets
        chunks = [clip(chunk, 360) for chunk in re.split(r"\s{2,}", transcript) if len(chunk.strip()) >= 40]
        return chunks[:6]

    def _extract_comment_snippets(self, markup: str) -> list[str]:
        data = self._extract_embedded_json(markup, "ytInitialData")
        if not data:
            return []
        snippets: list[str] = []
        stack: list = [data]
        while stack and len(snippets) < 8:
            current = stack.pop()
            if isinstance(current, dict):
                text_obj = current.get("simpleText") or current.get("text")
                if isinstance(text_obj, dict):
                    text = text_obj.get("simpleText") or ""
                    runs = text_obj.get("runs") or []
                    if runs and isinstance(runs, list):
                        text = "".join(run.get("text", "") for run in runs if isinstance(run, dict))
                elif isinstance(text_obj, str):
                    text = text_obj
                else:
                    text = ""
                text = clip(html.unescape(text.strip()), 360)
                if len(text) >= 16 and self.REVIEW_HINTS.search(text):
                    snippets.append(text)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
        return snippets

    def _extract_embedded_json(self, markup: str, variable_name: str) -> dict | None:
        marker = f"{variable_name}"
        start = markup.find(marker)
        if start < 0:
            return None
        brace_start = markup.find("{", start)
        if brace_start < 0:
            return None
        raw = self._extract_balanced_json(markup, brace_start)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _extract_balanced_json(self, text: str, start: int) -> str:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return ""
