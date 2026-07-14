from __future__ import annotations

import dataclasses
import unittest
from unittest.mock import patch

from backend.config import settings
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

    def test_create_model_router_keyword_never_uses_hybrid(self) -> None:
        # Even when Gemini/OpenAI keys exist, keyword mode must stay deterministic.
        router = create_model_router("keyword")
        self.assertIs(type(router), KeywordModelRouter)

    def test_create_model_router_hybrid_when_keys_present(self) -> None:
        boosted = dataclasses.replace(settings, gemini_api_key="fake-key")
        with patch("backend.model_router.settings", boosted):
            router = create_model_router("hybrid")
            self.assertIsInstance(router, HybridModelRouter)

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

    def test_gemini_cached_content_skips_cache_below_char_floor(self) -> None:
        router = HybridModelRouter.__new__(HybridModelRouter)
        with router._gemini_cached_content("too short", "system") as cache_name:
            self.assertIsNone(cache_name)

    def test_gemini_cached_content_disabled_via_settings(self) -> None:
        router = HybridModelRouter.__new__(HybridModelRouter)
        disabled = dataclasses.replace(settings, gemini_context_cache_enabled=False)
        with patch("backend.gemini_client.settings", disabled):
            with router._gemini_cached_content("x" * 20000, "system") as cache_name:
                self.assertIsNone(cache_name)

    def test_gemini_cached_content_falls_back_when_cache_create_fails(self) -> None:
        router = HybridModelRouter.__new__(HybridModelRouter)
        boosted = dataclasses.replace(settings, gemini_context_cache_min_chars=10, gemini_api_key="fake-key")
        with patch("backend.gemini_client.settings", boosted):
            with patch("backend.gemini_client.GeminiClient._new_client") as new_client:
                new_client.return_value.caches.create.side_effect = RuntimeError("cache unavailable")
                with router._gemini_cached_content("x" * 100, "system") as cache_name:
                    self.assertIsNone(cache_name)

    def test_keyword_router_image_spec_extraction_returns_empty(self) -> None:
        router = KeywordModelRouter()
        specs, highlights = router.extract_official_specs_from_images(
            "Test SKU",
            ["https://example.com/spec-image.jpg"],
            "https://example.com/product",
            category="Lens",
        )
        self.assertEqual(specs, [])
        self.assertEqual(highlights, [])


if __name__ == "__main__":
    unittest.main()
