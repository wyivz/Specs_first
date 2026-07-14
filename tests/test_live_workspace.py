from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from frontend.live_data import events_since, get_task_status
from frontend.state import compute_progress_value
from frontend.ui.live_workspace import _resolve_status, _sync_task_events
from frontend.ui.matrix import matrix_rows_to_dataframe_records


class ProgressValueTest(unittest.TestCase):
    def test_running_tolerates_blank_progress(self) -> None:
        value = compute_progress_value(
            "RUNNING",
            {"progress": "", "sku_index": 0, "total_skus": 2, "phase": 1},
            2,
        )
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 0.97)

    def test_done_is_full(self) -> None:
        self.assertEqual(compute_progress_value("DONE", {"progress": 0.2}, 1), 1.0)


class ResolveStatusTest(unittest.TestCase):
    def test_task_done_event_wins_over_running_api(self) -> None:
        status = _resolve_status(
            {"task_id": "t1", "state": "RUNNING", "error": ""},
            [{"event_type": "task_done", "message": "done", "payload": {}}],
        )
        self.assertEqual(status["state"], "DONE")

    def test_api_terminal_unchanged(self) -> None:
        status = _resolve_status(
            {"task_id": "t1", "state": "FAILED", "error": "boom"},
            [{"event_type": "phase_started", "message": "x", "payload": {}}],
        )
        self.assertEqual(status["state"], "FAILED")


class SyncTaskEventsTest(unittest.TestCase):
    @patch("frontend.ui.live_workspace.events_since")
    @patch("frontend.ui.live_workspace.drain_events")
    @patch("frontend.ui.live_workspace.st")
    def test_uses_in_process_event_delta(
        self,
        st_mod: MagicMock,
        drain: MagicMock,
        since: MagicMock,
    ) -> None:
        st_mod.session_state = {"seen_event_count": 2}
        drain.return_value = [{"event_type": "phase_started"}]
        since.return_value = ([{"event_type": "task_done"}], 5)

        events = _sync_task_events("task-1")

        drain.assert_called_once_with("task-1")
        since.assert_called_once_with("task-1", 2)
        self.assertEqual(st_mod.session_state["seen_event_count"], 5)
        self.assertEqual(events, [{"event_type": "task_done"}])


class LiveDataTest(unittest.TestCase):
    def test_get_task_status_and_events_since(self) -> None:
        from frontend.api_client import get_api_client

        api = get_api_client()
        task_id = api.start_task(query="罗技 G304", mode="mock")
        status = get_task_status(task_id)
        self.assertEqual(status["task_id"], task_id)
        self.assertIn(status["state"], {"PENDING", "RUNNING", "DONE", "FAILED"})

        first, total = events_since(task_id, 0)
        self.assertIsInstance(first, list)
        self.assertGreaterEqual(total, len(first))
        again, total2 = events_since(task_id, total)
        self.assertEqual(again, [])
        self.assertEqual(total2, total)


class MatrixDenseTest(unittest.TestCase):
    def test_dataframe_records_flatten_cells(self) -> None:
        rows = [
            {
                "sku": {"value": "A", "status": "normal"},
                "mount": {"value": "E", "status": "warning"},
            }
        ]
        records = matrix_rows_to_dataframe_records(rows)
        self.assertEqual(len(records), 1)
        self.assertTrue(any("A" in str(v) for v in records[0].values()))


if __name__ == "__main__":
    unittest.main()
