from __future__ import annotations

import unittest

from backend.candidate_processor import CandidateProcessor
from collectors.mock import MockCollector
from schemas import OfficialSpec, ProductCandidate
from schemas.category_profile import DynamicCategoryProfile


class CandidateProcessorAlignmentTest(unittest.TestCase):
    def test_align_keeps_specs_when_jit_slots_match(self) -> None:
        processor = CandidateProcessor(
            collector=MockCollector(),
            router=object(),
            emit=lambda *args, **kwargs: None,
            category_profile=DynamicCategoryProfile(
                category_label="镜头",
                slots=["focal_length", "max_aperture", "mount"],
                aliases={"maximum aperture": "max_aperture", "mount": "mount"},
                source="openai_jit",
            ),
        )
        specs = [
            OfficialSpec(name="maximum_aperture", value="f/1.2", unit="", source_url="https://sony.com"),
            OfficialSpec(name="mount", value="Sony E-mount", unit="", source_url="https://sony.com"),
        ]
        aligned, highlights = processor._align_specs_to_profile(specs, [], "Lens")
        names = {spec.name for spec in aligned}
        self.assertIn("max_aperture", names)
        self.assertIn("mount", names)
        self.assertGreaterEqual(len(aligned), 2)
        self.assertLessEqual(len(highlights), 12)

    def test_align_fallback_keeps_collected_specs_when_all_demoted(self) -> None:
        processor = CandidateProcessor(
            collector=MockCollector(),
            router=object(),
            emit=lambda *args, **kwargs: None,
            category_profile=DynamicCategoryProfile(
                category_label="镜头",
                slots=["parameter_a", "parameter_b"],
                source="openai_jit",
            ),
        )
        specs = [
            OfficialSpec(name="focal_length", value="50mm", unit="", source_url="https://sony.com"),
            OfficialSpec(name="max_aperture", value="f/1.2", unit="", source_url="https://sony.com"),
        ]
        aligned, _ = processor._align_specs_to_profile(specs, [], "Lens")
        self.assertGreaterEqual(len(aligned), 2)


if __name__ == "__main__":
    unittest.main()
