"""Local ASR (Automatic Speech Recognition) — manual trigger and pipeline fallback.

Called from the Streamlit "本地转写" panel, POST /asr/transcribe, or Bilibili/YouTube
subtitle fallbacks when native captions are unavailable.

Supported backends (checked in order):
1. SenseVoice-small  — preferred for Chinese/mixed content, fast on CPU
2. faster-whisper    — multilingual fallback

Install:
  pip install -e ".[asr]"       # yt-dlp + faster-whisper (multilingual)
  pip install -e ".[asr-zh]"    # above + funasr / SenseVoice (Chinese)
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_AUDIO_EXTS = (".m4a", ".webm", ".opus", ".mp3", ".wav", ".ogg", ".aac")
_MODEL_CACHE: dict[str, Any] = {}


@dataclass
class AsrResult:
    text: str
    backend: str
    audio_path: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


@dataclass(frozen=True)
class AsrReadiness:
    ready: bool
    backend: str | None
    yt_dlp: str  # cli | module | none
    missing: tuple[str, ...] = ()
    install_hint: str = ""
    pipeline_fallback_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "backend": self.backend or "none",
            "yt_dlp": self.yt_dlp,
            "missing": list(self.missing),
            "install_hint": self.install_hint,
            "pipeline_fallback_enabled": self.pipeline_fallback_enabled,
        }


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


def _yt_dlp_cli_available() -> bool:
    return shutil.which("yt-dlp") is not None


def _yt_dlp_module_available() -> bool:
    try:
        import yt_dlp  # noqa: F401

        return True
    except ImportError:
        return False


def available_backend() -> str | None:
    """Public helper so the UI can decide whether to show the ASR panel."""
    return _check_backend()


def check_readiness() -> AsrReadiness:
    """Report whether manual ASR and configured pipeline fallbacks can run."""
    from collectors.settings import settings

    backend = _check_backend()
    yt_dlp_cli = _yt_dlp_cli_available()
    yt_dlp_module = _yt_dlp_module_available()
    yt_dlp = "cli" if yt_dlp_cli else ("module" if yt_dlp_module else "none")

    missing: list[str] = []
    if backend is None:
        missing.append("asr_backend")
    if yt_dlp == "none":
        missing.append("yt_dlp")

    pipeline_fallback_enabled = settings.bilibili_asr_fallback or settings.youtube_asr_fallback
    ready = backend is not None and yt_dlp != "none"

    if ready:
        install_hint = ""
    elif not missing:
        install_hint = ""
    elif "asr_backend" in missing and "yt_dlp" in missing:
        install_hint = 'pip install -e ".[asr]"'
    elif "yt_dlp" in missing:
        install_hint = "pip install yt-dlp"
    else:
        install_hint = 'pip install -e ".[asr-zh]"  # Chinese, or pip install faster-whisper'

    return AsrReadiness(
        ready=ready,
        backend=backend,
        yt_dlp=yt_dlp,
        missing=tuple(missing),
        install_hint=install_hint,
        pipeline_fallback_enabled=pipeline_fallback_enabled,
    )


def _find_downloaded_audio(output_dir: Path) -> str:
    candidates = [path for path in output_dir.iterdir() if path.suffix.lower() in _AUDIO_EXTS]
    if not candidates:
        return ""
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _download_via_cli(url: str, output_dir: Path) -> str:
    result = subprocess.run(
        [
            "yt-dlp",
            "-f",
            "bestaudio[ext=m4a]/bestaudio/best",
            "--no-playlist",
            "--no-warnings",
            "--output",
            str(output_dir / "%(id)s.%(ext)s"),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        return ""
    return _find_downloaded_audio(output_dir)


def _download_via_module(url: str, output_dir: Path) -> str:
    import yt_dlp

    outtmpl = str(output_dir / "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return _find_downloaded_audio(output_dir)


def download_audio(url: str, output_dir: Path) -> str:
    """Download audio via yt-dlp CLI or Python module. Returns path or ''."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if _yt_dlp_cli_available():
        try:
            path = _download_via_cli(url, output_dir)
            if path:
                return path
        except subprocess.TimeoutExpired:
            return ""
    if _yt_dlp_module_available():
        try:
            return _download_via_module(url, output_dir)
        except Exception:
            return ""
    return ""


def _get_sensevoice_model() -> Any:
    if "sensevoice" not in _MODEL_CACHE:
        from funasr import AutoModel  # type: ignore[import]

        _MODEL_CACHE["sensevoice"] = AutoModel(
            model="iic/SenseVoiceSmall",
            trust_remote_code=True,
            disable_update=True,
        )
    return _MODEL_CACHE["sensevoice"]


def _get_faster_whisper_model() -> Any:
    if "faster-whisper" not in _MODEL_CACHE:
        from faster_whisper import WhisperModel  # type: ignore[import]

        _MODEL_CACHE["faster-whisper"] = WhisperModel("base", device="auto", compute_type="int8")
    return _MODEL_CACHE["faster-whisper"]


def transcribe_sensevoice(audio_path: str, language: str = "auto") -> str:
    """Transcribe using SenseVoice (funasr). Returns raw text."""
    model = _get_sensevoice_model()
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
    """Transcribe using faster-whisper. Returns raw text."""
    model = _get_faster_whisper_model()
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
    readiness = check_readiness()
    backend = readiness.backend
    if backend is None:
        return AsrResult(
            text="",
            backend="none",
            error=(
                "No ASR backend available. "
                + (readiness.install_hint or "Install funasr or faster-whisper.")
            ),
        )

    work_dir = output_dir or Path(tempfile.mkdtemp(prefix="specs_asr_"))
    audio_path = download_audio(url, work_dir)
    if not audio_path:
        hint = readiness.install_hint or "pip install yt-dlp"
        return AsrResult(
            text="",
            backend=backend,
            error=f"yt-dlp failed to download audio from {url}. Try: {hint}",
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
