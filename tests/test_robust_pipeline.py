from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.pipeline import SpecsFirstPipeline
from collectors.base import Collector
from schemas import OfficialSpec, ProductCandidate


class BrokenSecondSkuCollector(Collector):
    def discover_candidates(self, query: str, category: str) -> list[ProductCandidate]:
        return [
            ProductCandidate("Good Lens", "Good", category, "https://example.com/good", 0.9),
            ProductCandidate("Bad Lens", "Bad", category, "https://example.com/bad", 0.9),
        ]

    def collect_official_specs(self, candidate: ProductCandidate, **kwargs) -> tuple[list[OfficialSpec], list[str]]:
        if candidate.brand == "Bad":
            raise RuntimeError("simulated collector failure")
        return [OfficialSpec("parameter_a", "50mm", "", candidate.source_url)], []

    def collect_real_world_corpus(self, candidate: ProductCandidate, **kwargs):
        return []

    def collect_prices(self, candidate: ProductCandidate, **kwargs):
        return []


class RobustPipelineTest(unittest.TestCase):
    def test_one_sku_failure_does_not_stop_remaining_skus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = SpecsFirstPipeline(collector=BrokenSecondSkuCollector(), vault_path=Path(tmp))
            result = pipeline.run(
                "Robust test",
                "Lens",
                selected_skus=["Good Lens", "Bad Lens"],
            )
            self.assertEqual(result.state.value, "DONE")
            self.assertEqual(len(result.assets), 1)
            self.assertEqual(result.assets[0].sku, "Good Lens")
            self.assertTrue(any(event.event_type == "sku_failed" for event in result.events))


if __name__ == "__main__":
    unittest.main()
