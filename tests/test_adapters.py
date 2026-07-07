from __future__ import annotations

import unittest

from backend.model_router import _parse_json_payload
from backend.retry import retry_call
from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.jd import JdAdapter
from collectors.adapters.youtube import YouTubeAdapter
from collectors.http import FetchResult


class AdapterTest(unittest.TestCase):
    def test_bilibili_extracts_comment_like_snippets(self) -> None:
        adapter = BilibiliAdapter()
        markup = "<html>用户评论：大光圈紫边明显，对焦环阻尼偶尔卡顿。另一个缺点是对焦慢。</html>"
        evidence = adapter.extract_evidence("https://www.bilibili.com/video/BVtest", markup)
        self.assertTrue(evidence)
        self.assertTrue(any("紫边" in item.excerpt or "对焦" in item.excerpt for item in evidence))

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
