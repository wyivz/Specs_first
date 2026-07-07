from __future__ import annotations

import tempfile
import unittest

from backend.task_runner import TaskManager


class ApiTaskManagerTest(unittest.TestCase):
    def test_background_task_completes_with_mock_mode(self) -> None:
        manager = TaskManager()
        with tempfile.TemporaryDirectory() as tmp:
            task_id = manager.start_task(
                query="Zeiss 50mm 镜头",
                category="Lens",
                mode="mock",
                vault_path=tmp,
            )
            record = manager.get(task_id)
            assert record and record.thread
            record.thread.join(timeout=30)
            self.assertEqual(record.state, "DONE")
            assert record.result is not None
            self.assertEqual(len(record.result.matrix.rows), 3)
            self.assertTrue(record.result.output_paths)

    def test_discover_returns_candidates(self) -> None:
        manager = TaskManager()
        candidates = manager.discover("Zeiss 50mm 镜头", "Lens", mode="mock")
        self.assertGreaterEqual(len(candidates), 1)
        self.assertIn("sku", candidates[0])


if __name__ == "__main__":
    unittest.main()
