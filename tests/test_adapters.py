from __future__ import annotations

import unittest

from backend.model_router import _parse_json_payload
from backend.retry import retry_call
from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.jd import JdAdapter


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
