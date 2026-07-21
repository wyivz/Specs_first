from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from collectors.asr import (
    AsrReadiness,
    check_readiness,
    download_audio,
    transcribe_url,
)


class AsrReadinessTest(unittest.TestCase):
    def tearDown(self) -> None:
        import collectors.asr as asr_mod

        asr_mod.clear_readiness_cache()

    def test_check_readiness_reports_missing_deps(self) -> None:
        with patch("collectors.asr._probe_backend", return_value=None):
            with patch("collectors.asr._yt_dlp_cli_available", return_value=False):
                with patch("collectors.asr._yt_dlp_module_available", return_value=False):
                    readiness = check_readiness(force=True)
        self.assertFalse(readiness.ready)
        self.assertEqual(readiness.missing, ("asr_backend", "yt_dlp"))
        self.assertIn("pip install", readiness.install_hint)

    def test_check_readiness_is_fast_without_importing_funasr(self) -> None:
        import collectors.asr as asr_mod

        with patch.object(asr_mod, "_probe_backend", return_value="sensevoice"):
            with patch.object(asr_mod, "_yt_dlp_cli_available", return_value=True):
                with patch.object(asr_mod, "_ffmpeg_available", return_value=True):
                    with patch.object(asr_mod, "_resolve_backend") as resolve:
                        readiness = check_readiness(force=True)
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.backend, "sensevoice")
        resolve.assert_not_called()

    def test_check_readiness_ok_with_module_ytdlp(self) -> None:
        with patch("collectors.asr._probe_backend", return_value="faster-whisper"):
            with patch("collectors.asr._yt_dlp_cli_available", return_value=False):
                with patch("collectors.asr._yt_dlp_module_available", return_value=True):
                    readiness = check_readiness(force=True)
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.yt_dlp, "module")
        self.assertEqual(readiness.backend, "faster-whisper")


class AsrDownloadTest(unittest.TestCase):
    def test_download_audio_prefers_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            audio = work / "abc.m4a"
            audio.write_bytes(b"fake")

            with patch("collectors.asr._yt_dlp_cli_available", return_value=True):
                with patch("collectors.asr._download_via_cli", return_value=str(audio)) as cli:
                    with patch("collectors.asr._download_via_module") as module:
                        path = download_audio("https://example.com/v", work)
            self.assertEqual(path, str(audio))
            cli.assert_called_once()
            module.assert_not_called()

    def test_download_audio_falls_back_to_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            audio = work / "abc.webm"
            audio.write_bytes(b"fake")

            with patch("collectors.asr._yt_dlp_cli_available", return_value=False):
                with patch("collectors.asr._yt_dlp_module_available", return_value=True):
                    with patch("collectors.asr._download_via_module", return_value=str(audio)) as module:
                        path = download_audio("https://example.com/v", work)
            self.assertEqual(path, str(audio))
            module.assert_called_once()


class AsrTranscribeTest(unittest.TestCase):
    def test_transcribe_url_without_backend(self) -> None:
        with patch("collectors.asr.check_readiness") as readiness:
            readiness.return_value = AsrReadiness(
                ready=False,
                backend=None,
                yt_dlp="none",
                missing=("asr_backend", "yt_dlp"),
                install_hint='pip install -e ".[asr]"',
            )
            result = transcribe_url("https://example.com/v")
        self.assertFalse(result.ok)
        self.assertIn("pip install", result.error)

    def test_transcribe_url_happy_path(self) -> None:
        from collectors.asr import AsrResult

        with patch("collectors.asr.check_readiness") as readiness:
            readiness.return_value = AsrReadiness(
                ready=True,
                backend="faster-whisper",
                yt_dlp="module",
            )
            with patch("collectors.asr.download_audio", return_value="/tmp/a.m4a"):
                with patch(
                    "collectors.asr.transcribe_file",
                    return_value=AsrResult(text="hello world", backend="faster-whisper", audio_path="/tmp/a.m4a"),
                ):
                    result = transcribe_url("https://example.com/v", language="en")
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.backend, "faster-whisper")

    def test_transcribe_url_surfaces_ytdlp_error(self) -> None:
        with patch("collectors.asr.check_readiness") as readiness:
            readiness.return_value = AsrReadiness(ready=True, backend="sensevoice", yt_dlp="cli")
            with patch("collectors.asr.download_audio", return_value=""):
                with patch("collectors.asr.last_download_error", return_value="Sign in to confirm"):
                    result = transcribe_url("https://www.youtube.com/watch?v=abc")
        self.assertFalse(result.ok)
        self.assertIn("Sign in to confirm", result.error)
        self.assertIn("YOUTUBE_COOKIE", result.error)

    def test_strip_sensevoice_tags(self) -> None:
        from collectors.asr import _strip_sensevoice_tags

        raw = "<|en|><|EMO_UNKNOWN|><|Speech|><|withitn|>Yeah."
        self.assertEqual(_strip_sensevoice_tags(raw), "Yeah.")

    def test_write_netscape_cookies(self) -> None:
        from collectors.asr import _write_netscape_cookies

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.txt"
            written = _write_netscape_cookies("a=1; b=2", ".youtube.com", path)
            self.assertIsNotNone(written)
            text = path.read_text(encoding="utf-8")
            self.assertIn(".youtube.com", text)
            self.assertIn("\ta\t1", text)
            self.assertIn("\tb\t2", text)


class AsrPlatformHealthTest(unittest.TestCase):
    def test_platform_health_warns_when_fallback_enabled_but_not_ready(self) -> None:
        from collectors.settings import settings

        from backend.platform_health import check_asr_stack

        disabled = dataclasses.replace(settings, youtube_asr_fallback=True, bilibili_asr_fallback=False)
        with patch("collectors.settings.settings", disabled):
            with patch("collectors.asr.check_readiness") as readiness:
                readiness.return_value = AsrReadiness(
                    ready=False,
                    backend=None,
                    yt_dlp="none",
                    missing=("asr_backend", "yt_dlp"),
                    install_hint='pip install -e ".[asr]"',
                    pipeline_fallback_enabled=True,
                )
                check = check_asr_stack()
        self.assertEqual(check.status, "warn")
        self.assertIn("Pipeline ASR fallback", check.message)

    def test_platform_health_skips_when_optional_and_disabled(self) -> None:
        from collectors.settings import settings

        from backend.platform_health import check_asr_stack

        disabled = dataclasses.replace(settings, youtube_asr_fallback=False, bilibili_asr_fallback=False)
        with patch("collectors.settings.settings", disabled):
            with patch("collectors.asr.check_readiness") as readiness:
                readiness.return_value = AsrReadiness(
                    ready=False,
                    backend=None,
                    yt_dlp="none",
                    missing=("asr_backend", "yt_dlp"),
                    pipeline_fallback_enabled=False,
                )
                check = check_asr_stack()
        self.assertEqual(check.status, "skip")


if __name__ == "__main__":
    unittest.main()
