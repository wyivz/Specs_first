from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.gemini_client import (
    RECOMMENDED_GEMINI_MODEL,
    build_generation_config,
    is_gemini_3_family,
    is_retired_gemini_model,
    resolve_gemini_model,
)


class GeminiClientConfigTest(unittest.TestCase):
    def test_recommended_model_is_gemini_35_flash(self) -> None:
        self.assertEqual(RECOMMENDED_GEMINI_MODEL, "gemini-3.5-flash")

    def test_is_gemini_3_family(self) -> None:
        self.assertTrue(is_gemini_3_family("gemini-3.5-flash"))
        self.assertTrue(is_gemini_3_family("gemini-3-flash-preview"))
        self.assertFalse(is_gemini_3_family("gemini-2.5-flash"))

    def test_resolve_keeps_gemini_35_flash(self) -> None:
        with patch("backend.gemini_client.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-3.5-flash"
            self.assertEqual(resolve_gemini_model(), "gemini-3.5-flash")

    def test_resolve_upgrades_retired(self) -> None:
        with patch("backend.gemini_client.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-1.5-flash"
            self.assertEqual(resolve_gemini_model(), RECOMMENDED_GEMINI_MODEL)

    def test_build_generation_config_for_gemini_35_uses_thinking_level(self) -> None:
        with patch("backend.gemini_client.settings") as mock_settings:
            mock_settings.gemini_thinking_level = ""
            config = build_generation_config("json_extract", "gemini-3.5-flash")
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["thinking_config"]["thinking_level"], "low")
        self.assertNotIn("temperature", config)

    def test_probe_profile_uses_minimal_thinking(self) -> None:
        config = build_generation_config("probe", "gemini-3.5-flash")
        self.assertEqual(config["thinking_config"]["thinking_level"], "minimal")
        self.assertGreaterEqual(config["max_output_tokens"], 64)

    def test_is_retired_gemini_model(self) -> None:
        self.assertTrue(is_retired_gemini_model("gemini-2.0-flash"))
        self.assertFalse(is_retired_gemini_model("gemini-3.5-flash"))


if __name__ == "__main__":
    unittest.main()
