from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import Mock, patch

from collectors.credentials import BilibiliCredentials, TaobaoCredentials
from collectors.rate_limit import CollectionGuard, PlatformRateLimiter, get_collection_guard, platform_for_url


class CollectionInfraTest(unittest.TestCase):
    def test_platform_for_url(self) -> None:
        self.assertEqual(platform_for_url("https://www.bilibili.com/video/BV1xx"), "bilibili")
        self.assertEqual(platform_for_url("https://www.youtube.com/watch?v=abc"), "youtube")
        self.assertEqual(platform_for_url("https://item.jd.com/123.html"), "ecommerce")

    def test_rate_limiter_serializes_same_platform(self) -> None:
        limiter = PlatformRateLimiter(default_interval_seconds=0.05, platform_intervals={"http": 0.05})
        timestamps: list[float] = []

        def worker() -> None:
            limiter.wait("http")
            timestamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        timestamps.sort()
        self.assertGreaterEqual(timestamps[1] - timestamps[0], 0.04)

    def test_collection_guard_rejects_concurrent_use(self) -> None:
        guard = CollectionGuard()
        with guard:
            with self.assertRaises(RuntimeError):
                with guard:
                    pass

    def test_bilibili_credentials_configured(self) -> None:
        empty = BilibiliCredentials(sessdata="", bili_jct="", dedeuserid="")
        self.assertFalse(empty.configured)
        ready = BilibiliCredentials(sessdata="a", bili_jct="b", dedeuserid="c")
        self.assertTrue(ready.configured)

    def test_taobao_credentials_extract_sign_token(self) -> None:
        empty = TaobaoCredentials()
        self.assertFalse(empty.configured)
        ready = TaobaoCredentials(cookie="_m_h5_tk=abc123_token_part_1700; other=1")
        self.assertEqual(ready.sign_token(), "abc123")
        self.assertTrue(ready.configured)

    def test_get_collection_guard_is_singleton(self) -> None:
        self.assertIs(get_collection_guard(), get_collection_guard())


class HttpRateLimitTest(unittest.TestCase):
    @patch("collectors.rate_limit.get_rate_limiter")
    def test_http_fetch_waits_before_request(self, get_limiter) -> None:
        from collectors.http import HttpClient

        limiter = Mock()
        get_limiter.return_value = limiter
        client = HttpClient(retries=0)
        with patch.object(client, "_platform_for_url", return_value="youtube"):
            with patch("urllib.request.urlopen", side_effect=OSError("offline")):
                client.fetch("https://www.youtube.com/watch?v=abc")
        limiter.wait.assert_called_with("youtube")
