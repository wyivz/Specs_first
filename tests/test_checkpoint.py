from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.checkpoint import MemoryCheckpointStore, TaskCheckpoint
from backend.pipeline import SpecsFirstPipeline
from collectors.base import Collector
from collectors.browser import BrowserAuthRequired
from schemas import OfficialSpec, ProductCandidate, TaskState


class PausingCollector(Collector):
    def discover_candidates(self, query: str, category: str) -> list[ProductCandidate]:
        return [
            ProductCandidate(
                sku="Pause Test Lens",
                brand="Test",
                category=category,
                source_url="https://example.com/lens",
                confidence=0.9,
            )
        ]

    def collect_official_specs(self, candidate: ProductCandidate, **kwargs) -> tuple[list[OfficialSpec], list[str]]:
        return [OfficialSpec("focal_length", "50mm", "", candidate.source_url)], ["test highlight"]

    def collect_real_world_corpus(self, candidate: ProductCandidate, **kwargs):
        return []

    def collect_prices(self, candidate: ProductCandidate, **kwargs):
        raise BrowserAuthRequired(
            "Captcha required",
            url="https://item.jd.com/mock.html",
            storage_state_path=Path("vault_output/browser_captures/test_state.json"),
        )


class CheckpointTest(unittest.TestCase):
    def test_memory_checkpoint_roundtrip(self) -> None:
        store = MemoryCheckpointStore()
        checkpoint = TaskCheckpoint(
            task_id="task-1",
            query="Zeiss 50mm",
            category="Lens",
            mode="mock",
            vault_path="vault_output",
            selected_skus=["Zeiss Makro-Planar T* 50mm f/2"],
            next_candidate_index=1,
            pause_url="https://item.jd.com/mock.html",
            storage_state_path="vault_output/browser_captures/task-1.json",
        )
        store.save(checkpoint)
        loaded = store.load("task-1")
        assert loaded is not None
        self.assertEqual(loaded.task_id, "task-1")
        self.assertEqual(loaded.next_candidate_index, 1)
        self.assertEqual(loaded.pause_url, "https://item.jd.com/mock.html")

    def test_pipeline_pauses_and_resumes_after_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryCheckpointStore()
            pipeline = SpecsFirstPipeline(collector=PausingCollector(), vault_path=Path(tmp), checkpoint_store=store)
            paused = pipeline.run("Pause Test", "Lens", task_id="pause-task", use_browser=True)
            self.assertEqual(paused.state, TaskState.PAUSED_NEED_AUTH)
            checkpoint = store.load("pause-task")
            assert checkpoint is not None
            self.assertEqual(checkpoint.next_candidate_index, 0)
            self.assertTrue(checkpoint.in_progress_payload)

            with patch.object(PausingCollector, "collect_prices", return_value=[]):
                resumed = pipeline.run(checkpoint=checkpoint, use_browser=True)
            self.assertEqual(resumed.state, TaskState.DONE)
            self.assertIsNone(store.load("pause-task"))


if __name__ == "__main__":
    unittest.main()
