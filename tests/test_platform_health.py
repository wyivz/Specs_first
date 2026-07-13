from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.gemini_health import (
    RECOMMENDED_GEMINI_MODEL,
    build_gemini_health,
    is_retired_gemini_model,
    resolve_gemini_model,
)
from backend.platform_health import build_platform_health, check_gemini_model


class GeminiHealthTest(unittest.TestCase):
    def test_is_retired_gemini_model(self) -> None:
        self.assertTrue(is_retired_gemini_model("gemini-1.5-flash"))
        self.assertTrue(is_retired_gemini_model("gemini-2.0-flash-lite-001"))
        self.assertFalse(is_retired_gemini_model("gemini-2.5-flash"))

    def test_resolve_gemini_model_upgrades_retired_default(self) -> None:
        with patch("backend.gemini_health.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-1.5-flash"
            self.assertEqual(resolve_gemini_model(), RECOMMENDED_GEMINI_MODEL)

    def test_resolve_gemini_model_keeps_current(self) -> None:
        with patch("backend.gemini_health.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-3.5-flash"
            self.assertEqual(resolve_gemini_model(), "gemini-3.5-flash")

    def test_build_gemini_health_without_key(self) -> None:
        with patch("backend.gemini_health.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-2.5-flash"
            mock_settings.has_gemini = False
            status = build_gemini_health()
            self.assertTrue(status.healthy)
            self.assertIn("not set", status.message)

    def test_build_gemini_health_retired_model(self) -> None:
        with patch("backend.gemini_health.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-1.5-flash"
            mock_settings.has_gemini = True
            status = build_gemini_health()
            self.assertFalse(status.healthy)
            self.assertTrue(status.model_retired)


class PlatformHealthTest(unittest.TestCase):
    def test_build_platform_health_overall_ok_without_keys(self) -> None:
        with patch("backend.platform_health.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-2.5-flash"
            mock_settings.has_gemini = False
            mock_settings.has_openai = False
            mock_settings.openai_model = "gpt-4o-mini"
            mock_settings.default_mode = "mock"
            with patch("backend.platform_health.load_bilibili_credentials") as bilibili:
                with patch("backend.platform_health.load_taobao_credentials") as taobao:
                    bilibili.return_value.configured = False
                    taobao.return_value.configured = False
                    report = build_platform_health()
        self.assertIn(report.overall, {"ok", "degraded"})
        names = {item.name for item in report.checks}
        self.assertIn("gemini_model", names)
        self.assertIn("bilibili_credentials", names)

    def test_check_gemini_model_warns_on_retired_config(self) -> None:
        # check_gemini_model reads platform_health.settings; build_gemini_health
        # reads gemini_health.settings — both must be patched to the same mock.
        with patch("backend.gemini_health.settings") as mock_settings:
            mock_settings.gemini_model = "gemini-1.5-flash"
            mock_settings.has_gemini = True
            with patch("backend.platform_health.settings", mock_settings):
                result = check_gemini_model()
        self.assertEqual(result.status, "warn")
        self.assertEqual(result.details["effective_model"], RECOMMENDED_GEMINI_MODEL)
        self.assertEqual(result.details["configured_model"], "gemini-1.5-flash")


if __name__ == "__main__":
    unittest.main()
