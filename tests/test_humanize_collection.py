from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from collectors.rate_limit import HostBackoffTracker, PlatformRateLimiter, human_pause
from collectors.session_cache import (
    extract_m_h5_tk_from_storage_state,
    load_taobao_m_h5_tk,
    save_taobao_m_h5_tk,
)


class HumanizeCollectionTest(unittest.TestCase):
    def test_rate_limiter_applies_jitter_when_configured(self) -> None:
        limiter = PlatformRateLimiter(
            default_interval_seconds=0.0,
            platform_intervals={"ecommerce": 0.0},
            default_jitter=(0.05, 0.06),
        )
        started = __import__("time").monotonic()
        limiter.wait("ecommerce")
        elapsed = __import__("time").monotonic() - started
        self.assertGreaterEqual(elapsed, 0.04)

    def test_host_backoff_escalates_and_blocks(self) -> None:
        tracker = HostBackoffTracker(base_seconds=0.05, max_seconds=0.2)
        url = "https://pc-frequent-pro.pf.jd.com/?reason=403"
        first = tracker.note_rate_limited(url)
        self.assertAlmostEqual(first, 0.05, places=2)
        self.assertTrue(tracker.in_backoff("https://item.jd.com/1.html"))
        second = tracker.note_rate_limited(url)
        self.assertAlmostEqual(second, 0.1, places=2)
        remaining = tracker.remaining_seconds("https://item.jd.com/2.html")
        self.assertGreater(remaining, 0)
        tracker.wait_if_needed("https://item.jd.com/2.html")
        self.assertEqual(tracker.remaining_seconds("https://item.jd.com/2.html"), 0.0)

    def test_taobao_token_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.session_cache._taobao_token_path", return_value=Path(tmp) / "tk.cache"):
                save_taobao_m_h5_tk("abc123_456")
                self.assertEqual(load_taobao_m_h5_tk(), "abc123_456")

    def test_extract_token_from_storage_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                '{"cookies":[{"name":"_m_h5_tk","value":"tok_1","domain":".taobao.com"}]}',
                encoding="utf-8",
            )
            self.assertEqual(extract_m_h5_tk_from_storage_state(path), "tok_1")

    def test_mtop_first_price_skips_page_fetch(self) -> None:
        from collectors.adapters.jd import JdAdapter
        from collectors.adapters.registry import AdapterRegistry
        from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
        from collectors.credentials import TaobaoCredentials
        from collectors.http import SearchResult
        from collectors.sources.ecommerce import EcommerceSourceCollector
        from schemas import EvidenceItem, PriceFinding

        http = MagicMock()
        http.search.return_value = [
            SearchResult("键盘", "https://detail.tmall.com/item.htm?id=520813140663", "机械键盘"),
        ]
        registry = AdapterRegistry()
        registry.register(JdAdapter())
        tb = TmallTaobaoAdapter(
            credentials=TaobaoCredentials(
                cookie="_m_h5_tk=abc_1; cookie2=x",
                m_h5_tk="abc_1",
            )
        )
        registry.register(tb)
        collector = EcommerceSourceCollector(http=http, registry=registry, browser=MagicMock())
        collector.resilient.fetch = MagicMock(side_effect=AssertionError("page fetch must be skipped"))  # type: ignore[method-assign]
        evidence = EvidenceItem(
            platform="Taobao/Tmall",
            url="https://detail.tmall.com/item.htm?id=520813140663",
            author="Taobao/Tmall",
            locator="mtop",
            captured_at="2026-01-01T00:00:00Z",
            excerpt="mtop price",
            confidence=0.6,
        )
        finding = PriceFinding(
            platform="Taobao/Tmall",
            list_price=399.0,
            coupon_discount=0.0,
            subsidy_discount=0.0,
            cross_store_discount=0.0,
            final_price=399.0,
            screenshot_path="",
            captured_at="2026-01-01T00:00:00Z",
            evidence=evidence,
        )
        collector._try_taobao_mtop_price = MagicMock(return_value=finding)  # type: ignore[method-assign]
        candidate = type("C", (), {"sku": "机械键盘", "category": "Keyboard"})()
        prices = collector.collect(candidate)  # type: ignore[arg-type]
        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0].final_price, 399.0)
        collector.resilient.fetch.assert_not_called()

    def test_human_pause_is_bounded(self) -> None:
        started = __import__("time").monotonic()
        human_pause(0.01, 0.02)
        self.assertLess(__import__("time").monotonic() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
