from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

from collectors.adapters.jd import JdAdapter
from collectors.collection_trace import CollectionTrace, sanitize_log_text
from collectors.diagnostics import CollectorDiagnostics
from collectors.extractors import extract_price
from collectors.http_decompress import decode_http_text
from collectors.page_sanitize import detect_page_blockers
from collectors.rate_limit import HostBackoffTracker, reset_host_backoff_for_tests
from collectors.url_guards import is_noisy_forum_url
from schemas.category_profile import forum_search_queries


class HttpDecompressTest(unittest.TestCase):
    def test_gzip_roundtrip(self) -> None:
        raw = gzip.compress("Hello 尼康 Z5II 参数".encode("utf-8"))
        text, note = decode_http_text(raw, charset="utf-8", content_encoding="gzip")
        self.assertEqual(note, "gzip")
        self.assertIn("Z5II", text)

    def test_binary_rejected(self) -> None:
        # Synthetic high-control blob should not become fake HTML text.
        raw = bytes(range(32)) * 20
        text, note = decode_http_text(raw, charset="utf-8", content_encoding="")
        self.assertEqual(text, "")
        self.assertTrue(note)


class CollectionTraceSanitizeTest(unittest.TestCase):
    def test_strips_control_chars(self) -> None:
        dirty = "ok\x00\x08price ¥4599"
        self.assertEqual(sanitize_log_text(dirty), "okprice ¥4599")

    def test_timestamp_includes_date(self) -> None:
        diagnostics = CollectorDiagnostics()
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "trace.log"
            trace = CollectionTrace(diagnostics=diagnostics, log_path=log_path, task_id="t1")
            trace.log_fetch(
                "https://example.com",
                method="http",
                ok=False,
                preview="\x00\x08binary junk",
                sku="Z5II",
            )
            text = log_path.read_text(encoding="utf-8")
            self.assertRegex(text, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
            self.assertNotIn("\x00", text)
            self.assertNotIn("\x08", text)


class PriceExtractionGuardTest(unittest.TestCase):
    def test_rejects_coupon_floor_as_final(self) -> None:
        text = "满100减20 满200减50 商品编号100205228688 加入购物车"
        self.assertIsNone(extract_price(text))

    def test_accepts_currency_marked_price(self) -> None:
        text = "Nikon Z5II 全画幅微单 ¥12899 元 包邮"
        parsed = extract_price(text)
        assert parsed is not None
        self.assertGreaterEqual(parsed.final_price, 1000)

    def test_jd_sanitize_rejects_sku_fragment_list(self) -> None:
        adapter = JdAdapter()
        from collectors.extractors import ParsedPrice

        junk = ParsedPrice(28688.0, 0, 0, 0, 100.0)
        self.assertIsNone(adapter._sanitize_parsed_price(junk, "100205228688"))


class ForumRelevanceTest(unittest.TestCase):
    def test_nikon_uses_nikon_reddit(self) -> None:
        queries = dict(forum_search_queries("Z5II", include_reddit=True))
        self.assertIn("Reddit", queries)
        self.assertIn("Nikon", queries["Reddit"])
        self.assertNotIn("SonyAlpha", queries["Reddit"])

    def test_noisy_chiphell_index(self) -> None:
        self.assertTrue(is_noisy_forum_url("https://www.chiphell.com/"))
        self.assertTrue(
            is_noisy_forum_url("https://www.chiphell.com/forum.php?mod=forumdisplay&fid=53")
        )
        self.assertFalse(is_noisy_forum_url("https://www.chiphell.com/thread-2405391-1-1.html"))


class HostSoftFailTest(unittest.TestCase):
    def test_soft_fail_triggers_skip(self) -> None:
        reset_host_backoff_for_tests()
        tracker = HostBackoffTracker(soft_fail_limit=2, soft_fail_cooldown_seconds=30)
        url = "https://www.youtube.com/watch?v=abc"
        self.assertEqual(tracker.note_soft_failure(url, "auth_or_captcha"), 0.0)
        self.assertFalse(tracker.should_skip_host(url))
        self.assertGreater(tracker.note_soft_failure(url, "auth_or_captcha"), 0.0)
        self.assertTrue(tracker.should_skip_host(url))
        reset_host_backoff_for_tests()


class UndecodedBlockerTest(unittest.TestCase):
    def test_detects_binary_body(self) -> None:
        blockers = detect_page_blockers(
            "https://www.bilibili.com/video/BVxxx",
            markup="\x00\x08" * 100,
            text="\x00\x08" * 100,
        )
        kinds = {b.kind for b in blockers}
        self.assertIn("undecoded_content", kinds)


if __name__ == "__main__":
    unittest.main()
