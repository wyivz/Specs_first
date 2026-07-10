from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from collectors.adapters.youtube import YouTubeAdapter
from collectors.http import FetchResult


class FakeSnippet:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeFetchedTranscript:
    def __init__(self, parts: list[str]) -> None:
        self._parts = [FakeSnippet(text) for text in parts]

    def __iter__(self):
        return iter(self._parts)


class YouTubeTranscriptOptimizationTest(unittest.TestCase):
    def test_prefers_manual_track_over_auto_generated(self) -> None:
        adapter = YouTubeAdapter()
        tracks = [
            {
                "baseUrl": "https://www.youtube.com/api/timedtext?v=abc&lang=en",
                "languageCode": "en",
                "kind": "asr",
            },
            {
                "baseUrl": "https://www.youtube.com/api/timedtext?v=abc&lang=en&kind=manual",
                "languageCode": "en",
                "kind": "standard",
            },
        ]
        ordered = adapter._order_caption_tracks(tracks, ("en",))
        self.assertEqual(ordered[0]["kind"], "standard")

    def test_parse_webvtt_payload(self) -> None:
        adapter = YouTubeAdapter()
        payload = """WEBVTT

00:00:00.000 --> 00:00:02.000
Purple fringing is visible.

00:00:02.000 --> 00:00:04.000
Focus breathing remains an issue.
"""
        text = adapter._parse_caption_payload(payload)
        self.assertIn("Purple fringing", text)
        self.assertIn("Focus breathing", text)

    def test_download_caption_track_tries_multiple_formats(self) -> None:
        calls: list[str] = []

        class FakeHttp:
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
                calls.append(url)
                if "fmt=json3" in url:
                    return FetchResult(
                        url=url,
                        status=200,
                        text='{"events":[{"segs":[{"utf8":"json3 transcript"}]}]}',
                        content_type="application/json",
                    )
                return FetchResult(url=url, status=200, text="", content_type="")

        adapter = YouTubeAdapter(http=FakeHttp())  # type: ignore[arg-type]
        text = adapter._download_caption_track(
            {"baseUrl": "https://www.youtube.com/api/timedtext?v=abc&lang=en"},
            "https://www.youtube.com/watch?v=abc",
        )
        self.assertEqual(text, "json3 transcript")
        self.assertTrue(any("fmt=json3" in call for call in calls))

    @patch("collectors.adapters.youtube.YouTubeAdapter._fetch_transcript_object", return_value="")
    def test_fallback_uses_transcript_api_v1_fetch(self, _track_fetch) -> None:
        adapter = YouTubeAdapter()
        fake_api = MagicMock()
        fake_api.fetch.return_value = FakeFetchedTranscript(["api transcript text"])

        with patch("youtube_transcript_api.YouTubeTranscriptApi", return_value=fake_api):
            text = adapter._fetch_transcript_fallback("abc123", ("en",))

        self.assertEqual(text, "api transcript text")
        fake_api.fetch.assert_called_once()

    def test_translation_track_appends_tlang(self) -> None:
        adapter = YouTubeAdapter()
        track = {
            "baseUrl": "https://www.youtube.com/api/timedtext?v=abc&lang=en",
            "languageCode": "en",
            "isTranslatable": True,
        }

        class FakeHttp:
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
                if "tlang=zh-Hans" in url:
                    return FetchResult(
                        url=url,
                        status=200,
                        text='{"events":[{"segs":[{"utf8":"中文翻译字幕"}]}]}',
                        content_type="application/json",
                    )
                return FetchResult(url=url, status=200, text="", content_type="")

        adapter.http = FakeHttp()  # type: ignore[assignment]
        text = adapter._fetch_transcript_from_tracks(
            [track],
            watch_url="https://www.youtube.com/watch?v=abc",
            video_id="abc",
            preferred_languages=("zh", "en"),
        )
        self.assertEqual(text, "中文翻译字幕")


if __name__ == "__main__":
    unittest.main()
