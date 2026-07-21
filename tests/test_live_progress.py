from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.live_progress import (
    emit_live_step,
    extract_url,
    format_fetch_action,
    platform_label_from_url,
    reset_step_emitter,
    set_step_emitter,
    short_url_label,
)
from frontend.ui.live_workspace import _elapsed_seconds, _format_live_action


class LiveProgressHelpersTest(unittest.TestCase):
    def test_platform_label_and_fetch_action(self) -> None:
        url = "https://detail.tmall.com/item.htm?id=123"
        self.assertEqual(platform_label_from_url(url), "天猫")
        self.assertIn("天猫", format_fetch_action(url))
        self.assertTrue(short_url_label(url).startswith("天猫"))

    def test_extract_url(self) -> None:
        self.assertEqual(
            extract_url("抓取中 https://item.jd.com/100.html ok"),
            "https://item.jd.com/100.html",
        )

    def test_emit_live_step_via_contextvar(self) -> None:
        captured: list[tuple[str, dict]] = []

        def sink(action: str, payload: dict) -> None:
            captured.append((action, payload))

        token = set_step_emitter(sink)
        try:
            emit_live_step("Gemini 识图中", sku="ABC", url="https://item.jd.com/1.html", phase=1)
        finally:
            reset_step_emitter(token)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], "Gemini 识图中")
        self.assertEqual(captured[0][1]["sku"], "ABC")
        self.assertIn("started_at", captured[0][1])


class LiveStepUiTest(unittest.TestCase):
    def test_elapsed_and_format_line(self) -> None:
        started = (datetime.now(UTC) - timedelta(seconds=12)).isoformat()
        self.assertGreaterEqual(_elapsed_seconds(started), 11)
        line = _format_live_action(
            {
                "action": "抓取淘宝商品信息中",
                "url": "https://item.taobao.com/item.htm?id=1",
                "url_label": "淘宝/item.htm",
                "started_at": started,
                "detail": "",
            }
        )
        self.assertIn("抓取淘宝商品信息中", line)
        self.assertIn("](https://item.taobao.com/item.htm?id=1)", line)
        self.assertIn("已运行", line)


if __name__ == "__main__":
    unittest.main()
