from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.adapters.bilibili_api_client import BilibiliApiClient
from collectors.adapters.bilibili_guard import is_blocked_bvid, is_rickroll_title
from collectors.adapters.jd import JdAdapter
from collectors.collection_trace import CollectionTrace, create_collection_trace
from collectors.credentials import BilibiliCredentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import FetchResult, HttpClient


class CollectionTraceTest(unittest.TestCase):
    def test_trace_writes_human_readable_log(self) -> None:
        diagnostics = CollectorDiagnostics()
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "trace.log"
            trace = CollectionTrace(diagnostics=diagnostics, log_path=log_path, task_id="t1")
            trace.log_price("JD", "https://item.jd.com/1.html", source="mgets", final_price=4599.0, sku="Lens")
            self.assertTrue(log_path.exists())
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("[price]", text)
            self.assertIn("mgets", text)
            self.assertIn("4599", text)

    def test_create_collection_trace_respects_enabled_flag(self) -> None:
        diagnostics = CollectorDiagnostics()
        self.assertIsNone(create_collection_trace(diagnostics, enabled=False))


class BilibiliGuardTest(unittest.TestCase):
    def test_blocked_rickroll_bvid(self) -> None:
        self.assertTrue(is_blocked_bvid("BV1GJ411x7h7"))
        self.assertFalse(is_blocked_bvid("BV1ABCD12345"))

    def test_rickroll_title_detection(self) -> None:
        self.assertTrue(is_rickroll_title("Never Gonna Give You Up"))
        self.assertFalse(is_rickroll_title("Sony FE 50mm f1.2 GM review"))

    @patch.object(BilibiliApiClient, "fetch_subtitle_text", return_value="subtitle")
    @patch.object(BilibiliApiClient, "fetch_comment_texts", return_value=["comment"])
    def test_api_skips_blocked_bvid(self, _comments, _subtitle) -> None:
        client = BilibiliApiClient(credentials=BilibiliCredentials("s", "j", "d"))
        evidence = client.collect_api_evidence("https://www.bilibili.com/video/BV1GJ411x7h7")
        self.assertEqual(evidence, [])


class JdPriceFixTest(unittest.TestCase):
    def test_extract_price_prefers_main_cluster_over_noise(self) -> None:
        adapter = JdAdapter()
        markup = (
            '<script>{"price":"116","finalPrice":"116"}</script>'
            '<script>{"price":"4899","finalPrice":"4599"}</script>'
            "<div>到手价 4599 元</div>"
        )
        parsed = adapter.extract_price(markup)
        assert parsed is not None
        self.assertEqual(parsed.final_price, 4599.0)

    def test_fetch_price_from_mgets(self) -> None:
        adapter = JdAdapter()

        class FakeHttp:
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
                return FetchResult(
                    url=url,
                    status=200,
                    text='[{"id":"J_123","p":"4599.00","op":"4899.00"}]',
                    content_type="application/json",
                )

        parsed = adapter.fetch_price_from_mgets(FakeHttp(), "123")  # type: ignore[arg-type]
        assert parsed is not None
        self.assertEqual(parsed.final_price, 4599.0)
        self.assertEqual(parsed.list_price, 4899.0)

    def test_build_price_finding_prefers_mgets(self) -> None:
        adapter = JdAdapter()
        markup = '<script>{"price":"116"}</script><div>京东价 4599</div>'

        class FakeHttp:
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:
                return FetchResult(
                    url=url,
                    status=200,
                    text='[{"id":"J_123456","p":"4599.00","op":"4899.00"}]',
                    content_type="application/json",
                )

        finding = adapter.build_price_finding(
            "https://item.jd.com/123456.html",
            markup,
            http=FakeHttp(),  # type: ignore[arg-type]
        )
        assert finding is not None
        self.assertEqual(finding.final_price, 4599.0)


if __name__ == "__main__":
    unittest.main()
