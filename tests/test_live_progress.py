from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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

    def test_empty_started_at_is_zero_until_ensured(self) -> None:
        self.assertEqual(_elapsed_seconds(""), 0)
        line = _format_live_action({"action": "发现候选", "started_at": ""})
        self.assertIn("已运行 **0** 秒", line)


class ApplyEventTimerTest(unittest.TestCase):
    def _with_streamlit_session(self, session: dict):
        import sys
        from types import SimpleNamespace

        stub = SimpleNamespace(session_state=session)
        previous = sys.modules.get("streamlit")
        sys.modules["streamlit"] = stub  # type: ignore[assignment]
        return previous

    def _restore_streamlit(self, previous) -> None:
        import sys

        if previous is not None:
            sys.modules["streamlit"] = previous
        else:
            sys.modules.pop("streamlit", None)

    def test_phase_started_sets_started_at(self) -> None:
        from frontend.state import apply_event

        session = {
            "events_log": [],
            "total_steps": 1,
            "progress_info": {
                "sku": "",
                "sku_index": 0,
                "total_skus": 1,
                "phase": 0,
                "phase_label": "发现候选",
                "action": "发现候选",
                "started_at": "",
                "highlights": [],
            },
        }
        previous = self._with_streamlit_session(session)
        try:
            apply_event(
                {
                    "event_type": "phase_started",
                    "message": "发现候选",
                    "payload": {"phase": 0, "phase_label": "发现候选", "action": "发现候选"},
                }
            )
        finally:
            self._restore_streamlit(previous)
        self.assertTrue(session["progress_info"]["started_at"])

    def test_same_action_keeps_started_at(self) -> None:
        from frontend.state import apply_event

        started = "2026-07-21T00:00:00+00:00"
        session = {
            "events_log": [],
            "total_steps": 1,
            "progress_info": {
                "sku": "A",
                "sku_index": 0,
                "total_skus": 1,
                "phase": 2,
                "phase_label": "口碑",
                "action": "抓取B站商品信息中",
                "started_at": started,
                "highlights": [],
            },
        }
        previous = self._with_streamlit_session(session)
        try:
            apply_event(
                {
                    "event_type": "step_status",
                    "message": "抓取B站商品信息中",
                    "payload": {
                        "action": "抓取B站商品信息中",
                        "started_at": "2026-07-21T00:05:00+00:00",
                    },
                }
            )
        finally:
            self._restore_streamlit(previous)
        self.assertEqual(session["progress_info"]["started_at"], started)


if __name__ == "__main__":
    unittest.main()
