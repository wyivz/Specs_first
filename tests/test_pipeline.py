from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.pipeline import SpecsFirstPipeline
from schemas import CellStatus


class PipelineTest(unittest.TestCase):
    def test_mock_flow_outputs_evidence_matrix_and_obsidian_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = SpecsFirstPipeline(vault_path=Path(tmp))
            result = pipeline.run("罗技 G304 无线鼠标", "Product")

            self.assertEqual(result.state, "DONE")
            self.assertEqual(len(result.selected_candidates), 3)
            self.assertEqual(len(result.matrix.rows), 3)

            first_row = result.matrix.rows[0]
            self.assertEqual(first_row["critical_flaws"].status, CellStatus.WARNING)
            flaws = first_row["critical_flaws"].value.lower()
            self.assertTrue(
                any(token in flaws for token in ("defect", "battery", "endurance", "quality")),
                flaws,
            )
            self.assertTrue(first_row["critical_flaws"].evidence[0].url.startswith("https://"))
            self.assertIsInstance(first_row["price_real_world_min"].value, (int, float))
            self.assertGreater(first_row["price_real_world_min"].value, 0)
            self.assertEqual(first_row["arbitration_summary"].status, CellStatus.CONFLICT)
            self.assertIsInstance(first_row["evidence_confidence_avg"].value, float)

            output_text = "\n".join(path.read_text(encoding="utf-8") for path in result.output_paths)
            self.assertIn("```dataview", output_text)
            self.assertIn("Forum", output_text)
            self.assertIn("Video", output_text)
            self.assertIn("price_real_world_min:", output_text)
            self.assertIn("evidence_confidence_avg", output_text)
            self.assertTrue(
                any(token in output_text for token in ("罗技", "G304", "无线鼠标")),
                "Obsidian output should follow the query seed",
            )
            self.assertNotIn("Zeiss Makro-Planar", output_text)

            csv_paths = [path for path in result.output_paths if path.suffix == ".csv"]
            self.assertEqual(len(csv_paths), 1)
            csv_text = csv_paths[0].read_text(encoding="utf-8")
            self.assertIn("evidence_confidence_avg", csv_text)
            self.assertIn("price_real_world_min", csv_text)

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
