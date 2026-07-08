"""Local ASR (Automatic Speech Recognition) — manual-trigger fallback.

This module is intentionally NOT wired into the default pipeline.  It is
called explicitly when the user clicks "本地转写" in the UI, or via the
POST /asr/transcribe API endpoint.

Supported backends (checked in order):
1. SenseVoice-small  — preferred for Chinese/mixed content, fast on CPU
2. faster-whisper    — multilingual fallback

Neither backend is a hard dependency.  Missing backends are reported
gracefully so the rest of the application works without them.

yt-dlp is used to download the audio track; it is also optional.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AsrResult:
    text: str
    backend: str
    audio_path: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


def _check_backend() -> str | None:
    """Return 'sensevoice', 'faster-whisper', or None."""
    try:
        import funasr  # noqa: F401 — SenseVoice ships via funasr
        return "sensevoice"
    except ImportError:
        pass
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except ImportError:
        pass
    return None


def available_backend() -> str | None:
    """Public helper so the UI can decide whether to show the ASR button."""
    return _check_backend()


def download_audio(url: str, output_dir: Path) -> str:
    """Download audio via yt-dlp.  Returns local file path, or '' on failure."""
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "5",
                "--no-playlist",
                "--output", str(output_dir / "%(id)s.%(ext)s"),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return ""
        for path in output_dir.glob("*.mp3"):
            return str(path)
        for path in output_dir.glob("*.m4a"):
            return str(path)
        return ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def transcribe_sensevoice(audio_path: str, language: str = "auto") -> str:
    """Transcribe using SenseVoice (funasr).  Returns raw text."""
    from funasr import AutoModel  # type: ignore[import]

    model = AutoModel(
        model="iic/SenseVoiceSmall",
        trust_remote_code=True,
        disable_update=True,
    )
    result = model.generate(
        input=audio_path,
        language=language,
        use_itn=True,
        batch_size_s=60,
    )
    if not result:
        return ""
    texts = [item.get("text", "") for item in result if item.get("text")]
    return " ".join(texts).strip()


def transcribe_faster_whisper(audio_path: str, language: str | None = None) -> str:
    """Transcribe using faster-whisper.  Returns raw text."""
    from faster_whisper import WhisperModel  # type: ignore[import]

    model = WhisperModel("base", device="auto", compute_type="int8")
    segments, _ = model.transcribe(audio_path, language=language, beam_size=5)
    return " ".join(seg.text for seg in segments).strip()


def transcribe_url(
    url: str,
    *,
    output_dir: Path | None = None,
    language: str = "auto",
) -> AsrResult:
    """Top-level entry: download audio + transcribe.

    ``language`` values:
    - ``"auto"``  — auto-detect (SenseVoice) / None (faster-whisper)
    - ``"zh"``    — Chinese
    - ``"en"``    — English
    """
    backend = _check_backend()
    if backend is None:
        return AsrResult(
            text="",
            backend="none",
            error=(
                "No ASR backend available.  "
                "Install 'funasr' for SenseVoice or 'faster-whisper' for Whisper."
            ),
        )

    work_dir = output_dir or Path(tempfile.mkdtemp(prefix="specs_asr_"))
    audio_path = download_audio(url, work_dir)
    if not audio_path:
        return AsrResult(
            text="",
            backend=backend,
            error=f"yt-dlp failed to download audio from {url}.  Is yt-dlp installed?",
        )

    try:
        if backend == "sensevoice":
            lang = language if language != "auto" else "auto"
            text = transcribe_sensevoice(audio_path, language=lang)
        else:
            lang_fw = None if language == "auto" else language
            text = transcribe_faster_whisper(audio_path, language=lang_fw)
    except Exception as exc:
        return AsrResult(text="", backend=backend, audio_path=audio_path, error=str(exc))

    return AsrResult(text=text, backend=backend, audio_path=audio_path)
