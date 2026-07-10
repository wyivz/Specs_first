from __future__ import annotations

import unittest
from unittest.mock import patch

from collectors.adapters.youtube import YouTubeAdapter
from collectors.adapters.youtube_transcript_browser import (
    BrowserCaptionPayload,
    select_browser_transcript,
)


class YouTubeBrowserTranscriptTest(unittest.TestCase):
    def test_select_browser_transcript_prefers_manual_language(self) -> None:
        payloads = [
            BrowserCaptionPayload("en", "asr", '{"events":[{"segs":[{"utf8":"auto english"}]}]}'),
            BrowserCaptionPayload("en", "standard", '{"events":[{"segs":[{"utf8":"manual english"}]}]}'),
        ]

        def parse_payload(raw: str) -> str:
            if "manual english" in raw:
                return "manual english"
            if "auto english" in raw:
                return "auto english"
            return ""

        def language_matches(code: str, language: str) -> bool:
            return code.lower() == language.lower()

        text = select_browser_transcript(
            payloads,
            ("en",),
            parse_payload=parse_payload,
            language_matches=language_matches,
        )
        self.assertEqual(text, "manual english")

    @patch("collectors.adapters.youtube_transcript_browser.fetch_caption_payloads_in_browser")
    def test_adapter_uses_browser_transcript_path(self, browser_fetch) -> None:
        browser_fetch.return_value = [
            BrowserCaptionPayload(
                "en",
                "standard",
                '{"events":[{"segs":[{"utf8":"browser transcript text"}]}]}',
            )
        ]
        adapter = YouTubeAdapter()
        with patch("collectors.settings.settings") as mock_settings:
            mock_settings.youtube_browser_transcript = True
            text = adapter._fetch_transcript_via_browser(
                "https://www.youtube.com/watch?v=abc123",
                "abc123",
                ("en",),
            )
        self.assertIn("browser transcript text", text)
        browser_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
