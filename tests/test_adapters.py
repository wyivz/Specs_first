from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.model_router import _parse_json_payload
from backend.retry import retry_call
from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.bilibili_api_client import BilibiliApiClient
from collectors.adapters.jd import JdAdapter
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.credentials import TaobaoCredentials
from collectors.adapters.youtube import YouTubeAdapter
from collectors.adapters.youtube_comments import YouTubeCommentFetcher
from collectors.credentials import BilibiliCredentials
from collectors.http import FetchResult


class AdapterTest(unittest.TestCase):
    def test_bilibili_extracts_comment_like_snippets(self) -> None:
        adapter = BilibiliAdapter(credentials=BilibiliCredentials("", "", ""))
        markup = "<html>用户评论：对焦环阻尼偶尔卡顿。另一个缺点是对焦慢。</html>"
        evidence = adapter.extract_evidence("https://www.bilibili.com/video/BVtest", markup)
        self.assertTrue(evidence)
        self.assertTrue(any("卡顿" in item.excerpt or "对焦" in item.excerpt for item in evidence))

    def test_bilibili_api_client_extracts_bvid(self) -> None:
        self.assertEqual(
            BilibiliApiClient.extract_bvid("https://www.bilibili.com/video/BV1ABCD12345"),
            "BV1ABCD12345",
        )

    @patch.object(BilibiliApiClient, "fetch_subtitle_text", return_value="Overheating is visible during long recording.")
    @patch.object(BilibiliApiClient, "fetch_comment_texts", return_value=["Great product but lag remains an issue."])
    def test_bilibili_api_enrichment(self, _comments, _subtitle) -> None:
        client = BilibiliApiClient(
            credentials=BilibiliCredentials("s", "j", "d"),
            max_comments_per_video=10,
        )
        evidence = client.collect_api_evidence("https://www.bilibili.com/video/BV1ABCD12345")
        self.assertTrue(any("overheat" in item.excerpt.lower() or "lag" in item.excerpt.lower() for item in evidence))
        self.assertTrue(any(item.author == "bilibili_comment" for item in evidence))

    @patch.object(BilibiliApiClient, "collect_api_evidence")
    def test_bilibili_api_runs_without_use_browser(self, collect_api) -> None:
        from schemas import EvidenceItem

        collect_api.return_value = [
            EvidenceItem(
                platform="Bilibili",
                url="https://www.bilibili.com/video/BV1ABCD12345",
                author="bilibili_subtitle",
                locator="api-subtitle",
                excerpt="wide open overheating review",
                confidence=0.7,
                captured_at="2026-01-01T00:00:00Z",
            )
        ]
        adapter = BilibiliAdapter(credentials=BilibiliCredentials("s", "j", "d"))
        markup = "<html><head><title>SEL50F12GM 评测</title></head><body>开箱</body></html>"
        evidence = adapter.extract_evidence(
            "https://www.bilibili.com/video/BV1ABCD12345",
            markup,
            use_browser=False,
            sku="SEL50F12GM",
        )
        collect_api.assert_called_once()
        self.assertTrue(any("overheat" in item.excerpt.lower() for item in evidence))

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

        from collectors.settings import settings

        client = BilibiliApiClient(credentials=BilibiliCredentials("s", "j", "d"))
        disabled_settings = dataclasses.replace(settings, bilibili_asr_fallback=False)
        with patch("collectors.settings.settings", disabled_settings):
            text = client._fetch_subtitle_via_asr("BV1ABCD12345")
        self.assertEqual(text, "")

    def test_youtube_comment_fetcher_selects_review_comments(self) -> None:
        fetcher = YouTubeCommentFetcher(max_comments_per_video=5)
        selected = fetcher.select_review_comments(
            ["Great product", "Visible overheating under load", "Nice build"]
        )
        self.assertTrue(any("overheat" in item.lower() for item in selected))

    def test_jd_rejects_sku_fragment_and_tiny_final_price(self) -> None:
        adapter = JdAdapter()
        sku = "100010708487"
        markup = (
            f"<div>商品编号 {sku}</div>"
            '<script>{"price":"8487","finalPrice":"1"}</script>'
            "<span>京东价 8487</span>"
        )
        parsed = adapter.extract_price(markup, sku_id=sku)
        self.assertIsNone(parsed)

        finding = adapter.build_price_finding(f"https://item.jd.com/{sku}.html", markup)
        self.assertIsNone(finding)

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
        self.assertTrue(adapter.is_product_url("https://item.jd.com/123456.html"))
        self.assertFalse(adapter.is_product_url("https://campus.jd.com/"))
        self.assertFalse(adapter.is_product_url("https://music.jd.com/8_0_desc_0_0_1_15.html?key={keyword}"))
        self.assertFalse(adapter.is_product_url("https://www.jd.com/brand/abc.html"))

    def test_tmall_is_product_url(self) -> None:
        adapter = TmallTaobaoAdapter()
        self.assertTrue(adapter.is_product_url("https://detail.tmall.com/item.htm?id=1234567890"))
        self.assertFalse(adapter.is_product_url("https://world.taobao.com/lang/en-us/item/abc.htm"))

    def test_jd_detail_api_urls_include_desc_endpoints(self) -> None:
        adapter = JdAdapter()
        urls = adapter.detail_api_urls("https://item.jd.com/123456.html")
        self.assertTrue(any("description/channel" in url for url in urls))
        self.assertTrue(any("dx.3.cn/desc/123456" in url for url in urls))

    def test_tmall_taobao_adapter_extracts_desc_urls(self) -> None:
        adapter = TmallTaobaoAdapter()
        markup = '<script>var x={"descUrl":"//h5api.m.taobao.com/h5/mtop.taobao.detail.getdesc/6.0/?id=123"};</script>'
        urls = adapter.detail_api_urls("https://detail.tmall.com/item.htm?id=1234567890", markup)
        self.assertTrue(any("taobao.detail.getdesc" in url for url in urls))
        self.assertTrue(any("1234567890" in url for url in urls))

    def test_tmall_taobao_signed_urls_when_cookie_configured(self) -> None:
        credentials = TaobaoCredentials(
            cookie="_m_h5_tk=deadbeef1234567890abcdef_1700000000000; cookie2=1",
        )
        adapter = TmallTaobaoAdapter(credentials)
        urls = adapter.detail_api_urls("https://detail.tmall.com/item.htm?id=1234567890")
        self.assertTrue(any("sign=" in url for url in urls))
        self.assertTrue(any("data=" in url for url in urls))
        self.assertEqual(
            adapter.compute_sign("deadbeef1234567890abcdef", 1700000000000, {"id": "1234567890"}),
            adapter.compute_sign("deadbeef1234567890abcdef", 1700000000000, {"id": "1234567890"}),
        )

    def test_tmall_taobao_mtop_auth_error_raises_platform_auth(self) -> None:
        adapter = TmallTaobaoAdapter()
        payload = '{"ret":["FAIL_SYS_TOKEN_EMPTY::令牌为空"],"data":{}}'
        with self.assertRaises(Exception) as ctx:
            adapter.inspect_mtop_response(payload, url="https://item.taobao.com/item.htm?id=1")
        self.assertIn("Taobao/Tmall", str(ctx.exception))

    def test_tmall_taobao_extracts_script_price(self) -> None:
        adapter = TmallTaobaoAdapter()
        markup = '<script>{"subPrice":"3299","price":"3599"}</script><div>券后价 3299 元</div>'
        parsed = adapter.extract_price(markup)
        assert parsed is not None
        self.assertEqual(parsed.final_price, 3299.0)

    def test_tmall_taobao_unwraps_desc_payload(self) -> None:
        adapter = TmallTaobaoAdapter()
        payload = '{"data":{"pcDescContent":"<table><tr><td>重量</td><td>500g</td></tr></table>"}}'
        html = adapter.unwrap_desc_payload(payload)
        self.assertIn("重量", html)
        self.assertIn("500g", html)

    def test_tmall_taobao_normalize_item_url(self) -> None:
        adapter = TmallTaobaoAdapter()
        self.assertEqual(
            adapter.normalize_url("https://detail.tmall.com/item.htm?id=1234567890&foo=1"),
            "https://detail.tmall.com/item.htm?id=1234567890",
        )

    def test_youtube_extracts_transcript_snippets(self) -> None:
        caption_xml = """
        <transcript>
          <text start="0" dur="2">At wide open there is visible overheating in long clips.</text>
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
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
                if "timedtext" in url:
                    return FetchResult(url=url, status=200, text=caption_xml, content_type="text/xml")
                return FetchResult(url=url, status=404, text="", content_type="", error="not found")

        adapter = YouTubeAdapter(FakeHttp())  # type: ignore[arg-type]
        with patch.object(adapter.comment_fetcher, "fetch_comment_texts", return_value=[]):
            evidence = adapter.extract_evidence("https://www.youtube.com/watch?v=mock123", markup)
        self.assertTrue(any("overheat" in item.excerpt.lower() or "sticks" in item.excerpt.lower() for item in evidence))
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
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
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
