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
        use_browser: bool = True,
    ) -> list[EvidenceItem]:
        if not self.supports(url):
            return []
        video_id = self.extract_video_id(url)
        watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else url
        evidence = evidence_from_page("YouTube", watch_url, markup, confidence=confidence - 0.08)

        transcript = self.fetch_transcript(
            watch_url,
            markup=markup,
            video_id=video_id,
            allow_browser=use_browser,
        )
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

        # Comment downloader has no hard timeout and can stall Phase 2 for minutes.
        if use_browser:
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
        allow_browser: bool = True,
    ) -> str:
        resolved_id = video_id or self.extract_video_id(url)
        if not resolved_id:
            return ""
        watch_url = f"https://www.youtube.com/watch?v={resolved_id}"
        player = self._load_player_response(watch_url, markup=markup)
        if player:
            tracks = (
                player.get("captions", {})
                .get("playerCaptionsTracklistRenderer", {})
                .get("captionTracks", [])
            )
            if tracks:
                transcript = self._fetch_transcript_from_tracks(
                    tracks,
                    watch_url=watch_url,
                    video_id=resolved_id,
                    preferred_languages=preferred_languages,
                )
                if transcript:
                    return transcript
        if allow_browser:
            browser_transcript = self._fetch_transcript_via_browser(
                watch_url,
                resolved_id,
                preferred_languages,
            )
            if browser_transcript:
                return browser_transcript
        return self._fetch_transcript_fallback(
            resolved_id,
            preferred_languages,
            watch_url=watch_url,
            allow_browser=allow_browser,
        )

    def _load_player_response(self, watch_url: str, *, markup: str = "") -> dict | None:
        player = self._extract_embedded_json(markup, "ytInitialPlayerResponse") if markup else None
        if player is not None:
            return player
        page = self.http.fetch(watch_url, platform="youtube")
        if not page.ok:
            return None
        return self._extract_embedded_json(page.text, "ytInitialPlayerResponse")

    def _fetch_transcript_from_tracks(
        self,
        tracks: list[dict],
        *,
        watch_url: str,
        video_id: str,
        preferred_languages: tuple[str, ...],
    ) -> str:
        ordered_tracks = self._order_caption_tracks(tracks, preferred_languages)
        for track in ordered_tracks:
            transcript = self._download_caption_track(track, watch_url)
            if transcript:
                if self.diagnostics:
                    code = track.get("languageCode") or "unknown"
                    kind = track.get("kind") or "standard"
                    self.diagnostics.record(
                        "youtube",
                        f"captionTrack ok for {video_id}: lang={code} kind={kind} len={len(transcript)}",
                        level="info",
                    )
                return transcript
            if "&exp=xpe" in (track.get("baseUrl") or ""):
                if self.diagnostics:
                    self.diagnostics.record(
                        "youtube",
                        f"captionTrack for {video_id} requires PoToken (&exp=xpe); trying other tracks",
                        level="info",
                    )
        for track in ordered_tracks:
            if not track.get("isTranslatable"):
                continue
            for language in preferred_languages:
                tlang = self._youtube_tlang(language)
                if not tlang:
                    continue
                translated = dict(track)
                base_url = track.get("baseUrl") or ""
                if not base_url:
                    continue
                separator = "&" if "?" in base_url else "?"
                translated["baseUrl"] = f"{base_url}{separator}tlang={tlang}"
                transcript = self._download_caption_track(translated, watch_url)
                if transcript:
                    if self.diagnostics:
                        self.diagnostics.record(
                            "youtube",
                            f"translated caption ok for {video_id}: tlang={tlang} len={len(transcript)}",
                            level="info",
                        )
                    return transcript
        return ""

    def _fetch_transcript_via_browser(
        self,
        watch_url: str,
        video_id: str,
        preferred_languages: tuple[str, ...],
    ) -> str:
        from collectors.settings import settings

        if not settings.youtube_browser_transcript:
            return ""
        try:
            from collectors.adapters.youtube_transcript_browser import (
                fetch_caption_payloads_in_browser,
                select_browser_transcript,
            )
        except ImportError:
            return ""

        self.rate_limiter.wait("youtube")
        try:
            payloads = fetch_caption_payloads_in_browser(watch_url)
        except Exception as exc:
            self._record_transcript_info(video_id, f"browser transcript session failed for {video_id}: {exc}")
            return ""

        if not payloads:
            self._record_transcript_info(video_id, f"browser transcript returned no payloads for {video_id}")
            return ""

        transcript = select_browser_transcript(
            payloads,
            preferred_languages,
            parse_payload=self._parse_caption_payload,
            language_matches=self._language_matches_track_code,
        )
        if transcript:
            self._record_transcript_info(
                video_id,
                f"browser transcript ok for {video_id}: len={len(transcript)} payloads={len(payloads)}",
            )
            return transcript
        self._record_transcript_info(video_id, f"browser transcript payloads unparsable for {video_id}")
        return ""

    def _language_matches_track_code(self, track_code: str, language: str) -> bool:
        return self._language_matches_track({"languageCode": track_code}, language)

    def _download_caption_track(self, track: dict, watch_url: str) -> str:
        caption_url = track.get("baseUrl") or ""
        if not caption_url or "&exp=xpe" in caption_url:
            return ""
        headers = {
            "Referer": watch_url,
            "Origin": "https://www.youtube.com",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
        formats: tuple[str | None, ...] = ("json3", "srv3", "vtt", None)
        for fmt in formats:
            request_url = self._caption_url_with_fmt(caption_url, fmt)
            result = self.http.fetch(request_url, platform="youtube", extra_headers=headers)
            if not result.ok or not result.text.strip():
                continue
            transcript = self._parse_caption_payload(result.text)
            if transcript:
                return transcript
        return ""

    @staticmethod
    def _caption_url_with_fmt(caption_url: str, fmt: str | None) -> str:
        if fmt is None:
            return caption_url
        if re.search(r"(?:^|[?&])fmt=", caption_url):
            return re.sub(r"(?:^|[?&])fmt=[^&]+", f"fmt={fmt}", caption_url, count=1)
        separator = "&" if "?" in caption_url else "?"
        return f"{caption_url}{separator}fmt={fmt}"

    def _fetch_transcript_fallback(
        self,
        video_id: str,
        preferred_languages: tuple[str, ...],
        *,
        watch_url: str = "",
        allow_browser: bool = True,
    ) -> str:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import (
                IpBlocked,
                NoTranscriptFound,
                PoTokenRequired,
                RequestBlocked,
                TranscriptsDisabled,
                TranslationLanguageNotAvailable,
                VideoUnavailable,
            )
        except ImportError:
            return self._maybe_asr_fallback(watch_url or f"https://www.youtube.com/watch?v={video_id}", video_id)

        self.rate_limiter.wait("youtube")
        api = YouTubeTranscriptApi()
        languages = list(dict.fromkeys([*preferred_languages, "en"]))

        try:
            fetched = api.fetch(video_id, languages=languages)
            text = self._transcript_items_to_text(fetched)
            if text:
                return text
        except NoTranscriptFound:
            pass
        except (PoTokenRequired, RequestBlocked, IpBlocked) as exc:
            self._record_transcript_info(video_id, f"transcript-api blocked for {video_id}: {exc}")
            if allow_browser:
                browser_transcript = self._fetch_transcript_via_browser(
                    watch_url or f"https://www.youtube.com/watch?v={video_id}",
                    video_id,
                    preferred_languages,
                )
                if browser_transcript:
                    return browser_transcript
            return self._maybe_asr_fallback(
                watch_url or f"https://www.youtube.com/watch?v={video_id}",
                video_id,
            )
        except (TranscriptsDisabled, VideoUnavailable):
            return self._maybe_asr_fallback(
                watch_url or f"https://www.youtube.com/watch?v={video_id}",
                video_id,
            )
        except Exception as exc:
            self._record_transcript_info(video_id, f"transcript-api fetch failed for {video_id}: {exc}")

        try:
            transcript_list = api.list(video_id)
        except (PoTokenRequired, RequestBlocked, IpBlocked) as exc:
            self._record_transcript_info(video_id, f"transcript-api list blocked for {video_id}: {exc}")
            if allow_browser:
                browser_transcript = self._fetch_transcript_via_browser(
                    watch_url or f"https://www.youtube.com/watch?v={video_id}",
                    video_id,
                    preferred_languages,
                )
                if browser_transcript:
                    return browser_transcript
            return self._maybe_asr_fallback(
                watch_url or f"https://www.youtube.com/watch?v={video_id}",
                video_id,
            )
        except (TranscriptsDisabled, VideoUnavailable):
            return self._maybe_asr_fallback(
                watch_url or f"https://www.youtube.com/watch?v={video_id}",
                video_id,
            )
        except Exception as exc:
            self._record_transcript_info(video_id, f"transcript-api list failed for {video_id}: {exc}")
            return self._maybe_asr_fallback(
                watch_url or f"https://www.youtube.com/watch?v={video_id}",
                video_id,
            )

        try:
            transcript = transcript_list.find_transcript(languages)
        except NoTranscriptFound:
            transcript = next(iter(transcript_list), None)

        if transcript is not None:
            text = self._fetch_transcript_object(transcript, video_id)
            if text:
                return text

        for candidate in transcript_list:
            text = self._fetch_transcript_object(candidate, video_id)
            if text:
                return text

        for candidate in transcript_list:
            if not candidate.is_translatable:
                continue
            for language in preferred_languages:
                tlang = self._youtube_tlang(language)
                if not tlang:
                    continue
                try:
                    translated = candidate.translate(tlang)
                except TranslationLanguageNotAvailable:
                    continue
                text = self._fetch_transcript_object(translated, video_id)
                if text:
                    self._record_transcript_info(video_id, f"transcript-api translated to {tlang} for {video_id}")
                    return text

        return self._maybe_asr_fallback(
            watch_url or f"https://www.youtube.com/watch?v={video_id}",
            video_id,
        )

    def _fetch_transcript_object(self, transcript: object, video_id: str) -> str:
        try:
            from youtube_transcript_api._errors import PoTokenRequired

            fetched = transcript.fetch()  # type: ignore[attr-defined]
        except PoTokenRequired:
            return ""
        except Exception as exc:
            self._record_transcript_info(video_id, f"transcript-api track fetch failed for {video_id}: {exc}")
            return ""
        return self._transcript_items_to_text(fetched)

    def _transcript_items_to_text(self, fetched: object) -> str:
        parts: list[str] = []
        for item in fetched:  # type: ignore[operator]
            text = getattr(item, "text", None)
            if text is None and isinstance(item, dict):
                text = item.get("text")
            text = str(text or "").strip()
            if text:
                parts.append(text)
        return clip(" ".join(parts), 12000)

    def _maybe_asr_fallback(self, watch_url: str, video_id: str) -> str:
        from collectors.settings import settings

        if not settings.youtube_asr_fallback or not watch_url:
            return ""
        try:
            from collectors.asr import available_backend, transcribe_url
        except ImportError:
            return ""
        if not available_backend():
            self._record_transcript_info(
                video_id,
                "no captions and no local ASR backend installed; skipping YouTube transcript",
            )
            return ""
        self._record_transcript_info(video_id, f"falling back to local ASR for {video_id}")
        result = transcribe_url(watch_url, language="auto")
        if result.ok and result.text.strip():
            return clip(result.text.strip(), 12000)
        if result.error:
            self._record_transcript_info(video_id, f"ASR fallback failed for {video_id}: {result.error}")
        return ""

    def _record_transcript_info(self, video_id: str, message: str) -> None:
        if self.diagnostics:
            self.diagnostics.record("youtube", message, level="info")

    def _order_caption_tracks(
        self,
        tracks: list[dict],
        preferred_languages: tuple[str, ...],
    ) -> list[dict]:
        manual = [track for track in tracks if (track.get("kind") or "").lower() != "asr"]
        generated = [track for track in tracks if (track.get("kind") or "").lower() == "asr"]
        ordered: list[dict] = []
        seen: set[str] = set()
        for pool in (manual, generated):
            for language in preferred_languages:
                for track in pool:
                    track_id = track.get("baseUrl") or track.get("languageCode") or ""
                    if track_id in seen:
                        continue
                    if self._language_matches_track(track, language):
                        ordered.append(track)
                        seen.add(track_id)
        for track in tracks:
            track_id = track.get("baseUrl") or track.get("languageCode") or ""
            if track_id not in seen:
                ordered.append(track)
                seen.add(track_id)
        return ordered

    def _language_matches_track(self, track: dict, language: str) -> bool:
        code = (track.get("languageCode") or "").lower()
        target = language.lower()
        if code == target or code.startswith(f"{target}-"):
            return True
        vss = (track.get("vssId") or "").lower()
        if target == "zh" and (vss.startswith(".zh") or "zh" in vss):
            return True
        return target in vss

    @staticmethod
    def _youtube_tlang(language: str) -> str:
        normalized = language.lower()
        mapping = {
            "zh": "zh-Hans",
            "zh-hans": "zh-Hans",
            "zh-cn": "zh-Hans",
            "zh-hant": "zh-Hant",
            "zh-tw": "zh-Hant",
            "en": "en",
            "ja": "ja",
        }
        return mapping.get(normalized, language if len(language) >= 2 else "")

    def _pick_caption_track(self, tracks: list[dict], preferred_languages: tuple[str, ...]) -> dict | None:
        ordered = self._order_caption_tracks(tracks, preferred_languages)
        return ordered[0] if ordered else None

    def _parse_caption_payload(self, payload: str) -> str:
        payload = payload.strip()
        if not payload:
            return ""
        if payload.startswith("WEBVTT"):
            parts: list[str] = []
            for line in payload.splitlines():
                line = line.strip()
                if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
                    continue
                if re.match(r"^\d{2}:\d{2}:\d{2}\.", line):
                    continue
                parts.append(html.unescape(line))
            return clip(" ".join(parts), 12000)
        if payload.startswith("{"):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return ""
            events = data.get("events") or []
            parts = []
            for event in events:
                for segment in event.get("segs") or []:
                    text = segment.get("utf8") or ""
                    if text.strip() and text.strip() != "\n":
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
        # Do not fall back to arbitrary transcript chunks — they are often unrelated.
        return []

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
