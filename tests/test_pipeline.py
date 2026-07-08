from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.pipeline import SpecsFirstPipeline
from schemas import CellStatus


class PipelineTest(unittest.TestCase):
    def test_zeiss_mock_flow_outputs_evidence_matrix_and_obsidian_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = SpecsFirstPipeline(vault_path=Path(tmp))
            result = pipeline.run("Zeiss 50mm 镜头", "Lens")

            self.assertEqual(result.state, "DONE")
            self.assertEqual(len(result.selected_candidates), 3)
            self.assertEqual(len(result.matrix.rows), 3)

            zeiss_row = result.matrix.rows[0]
            self.assertEqual(zeiss_row["critical_flaws"].status, CellStatus.WARNING)
            # Title has been generalized to be category-agnostic
            self.assertIn("tradeoff", zeiss_row["critical_flaws"].value.lower())
            self.assertTrue(zeiss_row["critical_flaws"].evidence[0].url.startswith("https://"))
            self.assertEqual(zeiss_row["price_real_world_min"].value, 4899)
            self.assertEqual(zeiss_row["arbitration_summary"].status, CellStatus.CONFLICT)

            output_text = "\n".join(path.read_text(encoding="utf-8") for path in result.output_paths)
            self.assertIn("```dataview", output_text)
            self.assertIn("Bilibili", output_text)
            self.assertIn("Chiphell", output_text)
            self.assertIn("price_real_world_min: 4899", output_text)

    def test_findings_without_evidence_are_rejected(self) -> None:
        from schemas import ConflictLevel, RealWorldFinding

        with self.assertRaises(ValueError):
            RealWorldFinding(
                title="Unsupported flaw",
                detail="No URL",
                condition="unknown",
                frequency="unknown",
                severity=ConflictLevel.MINOR,
                evidence=[],
            )


if __name__ == "__main__":
    unittest.main()
