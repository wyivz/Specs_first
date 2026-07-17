from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.env_schema import SECRET_KEYS, parse_env_example
from collectors.env_store import (
    AssignmentLine,
    CommentLine,
    apply_updates,
    bootstrap_env_from_example,
    read_env_document,
    read_env_file,
    write_env_file,
)


class EnvStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.env_path = self.root / ".env"
        self.example_path = self.root / ".env.example"
        self.example_path.write_text(
            "\n".join(
                [
                    "# AI keys",
                    "OPENAI_API_KEY=",
                    "GEMINI_API_KEY=old-gemini",
                    "SPECS_FIRST_MODE=mock",
                    "",
                    "# --- Collection layer ---",
                    "COLLECTION_MIN_INTERVAL_SECONDS=1.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.env_path.write_text(self.example_path.read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_apply_updates_preserves_comments(self) -> None:
        with patch("collectors.env_store.env_example_path", return_value=self.example_path):
            with patch("collectors.env_store.dotenv_path", return_value=self.env_path):
                apply_updates({"COLLECTION_MIN_INTERVAL_SECONDS": "2.5"})
                raw = self.env_path.read_text(encoding="utf-8")
                self.assertIn("# AI keys", raw)
                self.assertIn("COLLECTION_MIN_INTERVAL_SECONDS=2.5", raw)

    def test_skip_empty_secrets_does_not_wipe_existing(self) -> None:
        with patch("collectors.env_store.env_example_path", return_value=self.example_path):
            with patch("collectors.env_store.dotenv_path", return_value=self.env_path):
                apply_updates(
                    {"GEMINI_API_KEY": ""},
                    skip_empty_secrets=True,
                    secret_keys=SECRET_KEYS,
                )
                values = read_env_file(self.env_path)
                self.assertEqual(values.get("GEMINI_API_KEY"), "old-gemini")

    def test_bootstrap_creates_env_from_example(self) -> None:
        missing = self.root / "missing.env"
        with patch("collectors.env_store.env_example_path", return_value=self.example_path):
            with patch("collectors.env_store.dotenv_path", return_value=missing):
                bootstrap_env_from_example(path=missing)
                self.assertTrue(missing.is_file())
                self.assertIn("OPENAI_API_KEY=", missing.read_text(encoding="utf-8"))

    def test_write_env_file_is_atomic(self) -> None:
        document = [
            CommentLine("# test"),
            AssignmentLine(key="FOO", value="bar"),
        ]
        write_env_file(document, path=self.env_path)
        self.assertEqual(read_env_file(self.env_path).get("FOO"), "bar")
        self.assertFalse(self.env_path.with_suffix(".env.tmp").exists())


class ReloadSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.env_path = self.root / ".env"
        self.env_path.write_text("GEMINI_API_KEY=reload-test\nCOLLECTION_MIN_INTERVAL_SECONDS=9\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reload_settings_rebuilds_singleton(self) -> None:
        import collectors.settings as settings_mod

        def _temp_dotenv() -> Path:
            return self.env_path

        with patch.object(settings_mod, "dotenv_path", _temp_dotenv):
            settings_mod.reload_settings(overwrite_all=True)
            self.assertEqual(settings_mod.settings.gemini_api_key, "reload-test")
            self.assertEqual(settings_mod.settings.collection_min_interval_seconds, 9.0)
            self.assertEqual(os.environ.get("GEMINI_API_KEY"), "reload-test")


class EnvSchemaTest(unittest.TestCase):
    def test_parse_env_example_matches_repo_template(self) -> None:
        specs = parse_env_example()
        keys = [spec.key for spec in specs]
        self.assertIn("OPENAI_API_KEY", keys)
        self.assertIn("JD_COOKIE", keys)
        self.assertIn("COLLECTION_TRACE", keys)
        self.assertGreaterEqual(len(specs), 35)


if __name__ == "__main__":
    unittest.main()
