from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.model_router import _parse_json_payload
from backend.retry import retry_call
from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.bilibili_api_client import BilibiliApiClient
from collectors.adapters.jd import JdAdapter
from collectors.adapters.youtube import YouTubeAdapter
from collectors.adapters.youtube_comments import YouTubeCommentFetcher
from collectors.credentials import BilibiliCredentials
from collectors.http import FetchResult


class AdapterTest(unittest.TestCase):
    def test_bilibili_extracts_comment_like_snippets(self) -> None:
        adapter = BilibiliAdapter(credentials=BilibiliCredentials("", "", ""))
        markup = "<html>用户评论：大光圈紫边明显，对焦环阻尼偶尔卡顿。另一个缺点是对焦慢。</html>"
        evidence = adapter.extract_evidence("https://www.bilibili.com/video/BVtest", markup)
        self.assertTrue(evidence)
        self.assertTrue(any("紫边" in item.excerpt or "对焦" in item.excerpt for item in evidence))

    def test_bilibili_api_client_extracts_bvid(self) -> None:
        self.assertEqual(
            BilibiliApiClient.extract_bvid("https://www.bilibili.com/video/BV1ABCD12345"),
            "BV1ABCD12345",
        )

    @patch.object(BilibiliApiClient, "fetch_subtitle_text", return_value="Purple fringing is visible at wide open.")
    @patch.object(BilibiliApiClient, "fetch_comment_texts", return_value=["Great lens but fringing remains an issue."])
    def test_bilibili_api_enrichment(self, _comments, _subtitle) -> None:
        client = BilibiliApiClient(
            credentials=BilibiliCredentials("s", "j", "d"),
            max_comments_per_video=10,
        )
        evidence = client.collect_api_evidence("https://www.bilibili.com/video/BV1ABCD12345")
        self.assertTrue(any("fringing" in item.excerpt.lower() for item in evidence))
        self.assertTrue(any(item.author == "bilibili_comment" for item in evidence))

    def test_bilibili_subtitle_asr_fallback_when_no_native_subtitle(self) -> None:
        client = BilibiliApiClient(credentials=BilibiliCredentials("s", "j", "d"))
        fake_asr_module = type(
            "FakeAsrModule",
            (),
            {
                "available_backend": staticmethod(lambda: "faster-whisper"),
                "transcribe_url": staticmethod(
                    lambda url, **kwargs: type(
                        "R", (), {"ok": True, "text": "audio transcript text", "error": ""}
                    )()
                ),
            },
        )
        with patch.dict("sys.modules", {"collectors.asr": fake_asr_module}):
            text = client._fetch_subtitle_via_asr("BV1ABCD12345")
        self.assertEqual(text, "audio transcript text")

    def test_bilibili_subtitle_asr_fallback_disabled(self) -> None:
        import dataclasses

        from backend.config import settings

        client = BilibiliApiClient(credentials=BilibiliCredentials("s", "j", "d"))
        disabled_settings = dataclasses.replace(settings, bilibili_asr_fallback=False)
        with patch("backend.config.settings", disabled_settings):
            text = client._fetch_subtitle_via_asr("BV1ABCD12345")
        self.assertEqual(text, "")

    def test_youtube_comment_fetcher_selects_review_comments(self) -> None:
        fetcher = YouTubeCommentFetcher(max_comments_per_video=5)
        selected = fetcher.select_review_comments(
            ["Great lens", "Visible purple fringing under backlight", "Nice build"]
        )
        self.assertTrue(any("fringing" in item.lower() for item in selected))

    def test_jd_extracts_script_price(self) -> None:
        adapter = JdAdapter()
        markup = '<script>{"price":"4899","finalPrice":"4599"}</script><div>到手价 4599 元</div>'
        parsed = adapter.extract_price(markup)
        assert parsed is not None
        self.assertEqual(parsed.final_price, 4599.0)

    def test_jd_normalize_item_url(self) -> None:
        adapter = JdAdapter()
        self.assertEqual(
            adapter.normalize_url("https://item.jd.com/123456.html?foo=1"),
            "https://item.jd.com/123456.html",
        )

    def test_jd_detail_api_urls_include_desc_endpoints(self) -> None:
        adapter = JdAdapter()
        urls = adapter.detail_api_urls("https://item.jd.com/123456.html")
        self.assertTrue(any("description/channel" in url for url in urls))
        self.assertTrue(any("dx.3.cn/desc/123456" in url for url in urls))

    def test_youtube_extracts_transcript_snippets(self) -> None:
        caption_xml = """
        <transcript>
          <text start="0" dur="2">At wide open there is visible purple fringing in backlit scenes.</text>
          <text start="2" dur="2">The focus ring feels inconsistent and sometimes sticks.</text>
        </transcript>
        """
        player = {
            "captions": {
                "playerCaptionsTracklistRenderer": {
                    "captionTracks": [
                        {
                            "baseUrl": "https://www.youtube.com/api/timedtext?v=mock123&lang=en",
                            "languageCode": "en",
                        }
                    ]
                }
            }
        }
        markup = (
            "<html><script>var ytInitialPlayerResponse = "
            + __import__("json").dumps(player)
            + ";</script></html>"
        )

        class FakeHttp:
            def fetch(self, url: str) -> FetchResult:
                if "timedtext" in url:
                    return FetchResult(url=url, status=200, text=caption_xml, content_type="text/xml")
                return FetchResult(url=url, status=404, text="", content_type="", error="not found")

        adapter = YouTubeAdapter(FakeHttp())  # type: ignore[arg-type]
        with patch.object(adapter.comment_fetcher, "fetch_comment_texts", return_value=[]):
            evidence = adapter.extract_evidence("https://www.youtube.com/watch?v=mock123", markup)
        self.assertTrue(any("purple fringing" in item.excerpt.lower() for item in evidence))
        self.assertTrue(any(item.locator.startswith("transcript-snippet") for item in evidence))

    def test_youtube_fetch_transcript_picks_preferred_language(self) -> None:
        player = {
            "captions": {
                "playerCaptionsTracklistRenderer": {
                    "captionTracks": [
                        {"baseUrl": "https://www.youtube.com/api/timedtext?v=abc&lang=ja", "languageCode": "ja"},
                        {"baseUrl": "https://www.youtube.com/api/timedtext?v=abc&lang=en", "languageCode": "en"},
                    ]
                }
            }
        }
        markup = f"<html><script>ytInitialPlayerResponse = {__import__('json').dumps(player)};</script></html>"
        fetched: list[str] = []

        class FakeHttp:
            def fetch(self, url: str) -> FetchResult:
                fetched.append(url)
                if "lang=en" in url:
                    return FetchResult(
                        url=url,
                        status=200,
                        text='{"events":[{"segs":[{"utf8":"Focus breathing is noticeable."}]}]}',
                        content_type="application/json",
                    )
                return FetchResult(url=url, status=404, text="", content_type="", error="not found")

        adapter = YouTubeAdapter(FakeHttp())  # type: ignore[arg-type]
        transcript = adapter.fetch_transcript("https://www.youtube.com/watch?v=abc", markup=markup)
        self.assertIn("Focus breathing", transcript)
        self.assertTrue(any("lang=en" in url for url in fetched))


class RobustJsonTest(unittest.TestCase):
    def test_parse_json_payload_recovers_embedded_object(self) -> None:
        payload = _parse_json_payload('noise {"findings": []} tail', default={"findings": ["x"]})
        self.assertEqual(payload["findings"], [])

    def test_retry_call_eventually_succeeds(self) -> None:
        state = {"count": 0}

        def flaky() -> str:
            state["count"] += 1
            if state["count"] < 2:
                raise RuntimeError("temporary")
            return "ok"

        self.assertEqual(retry_call(flaky, attempts=3, base_delay_seconds=0), "ok")


if __name__ == "__main__":
    unittest.main()
