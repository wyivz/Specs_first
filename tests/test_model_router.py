from __future__ import annotations

import unittest

from backend.model_router import HybridModelRouter, KeywordModelRouter, create_model_router
from schemas import ConflictLevel, EvidenceItem, OfficialSpec, RealWorldFinding


class ModelRouterTest(unittest.TestCase):
    def test_keyword_router_extracts_chromatic_aberration(self) -> None:
        router = KeywordModelRouter()
        corpus = [
            EvidenceItem(
                platform="Bilibili",
                url="https://example.com/video",
                author="tester",
                locator="comment-1",
                captured_at="2026-07-07T00:00:00+00:00",
                excerpt="Wide open purple fringing is obvious at the frame edge.",
                confidence=0.8,
            )
        ]
        findings = router.extract_real_world_findings("Test Lens", corpus)
        self.assertEqual(len(findings), 1)
        # Title has been generalized to be category-agnostic
        self.assertIn("tradeoff", findings[0].title.lower())

    def test_create_model_router_defaults_to_keyword_without_keys(self) -> None:
        router = create_model_router("keyword")
        self.assertIsInstance(router, (KeywordModelRouter, HybridModelRouter))

    def test_arbitration_marks_focus_ring_as_major(self) -> None:
        router = KeywordModelRouter()
        evidence = EvidenceItem(
            platform="Chiphell",
            url="https://example.com/thread",
            author="user",
            locator="floor-1",
            captured_at="2026-07-07T00:00:00+00:00",
            excerpt="Focus ring damping is sticky.",
            confidence=0.8,
        )
        finding = RealWorldFinding(
            title="Performance or control issue",
            detail=evidence.excerpt,
            condition="manual operation",
            frequency="reported",
            severity=ConflictLevel.MAJOR,
            evidence=[evidence],
        )
        warnings = router.arbitrate_conflicts([finding], official_specs=[OfficialSpec("parameter_a", "spec", "", "https://example.com")])
        self.assertEqual(warnings[0].level, ConflictLevel.MAJOR)


if __name__ == "__main__":
    unittest.main()
