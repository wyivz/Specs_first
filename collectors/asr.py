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

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_AUDIO_EXTS = (".m4a", ".webm", ".opus", ".mp3", ".wav", ".ogg", ".aac")
_MODEL_CACHE: dict[str, Any] = {}
_LAST_DOWNLOAD_ERROR = ""


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
    ffmpeg: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "backend": self.backend or "none",
            "yt_dlp": self.yt_dlp,
            "missing": list(self.missing),
            "install_hint": self.install_hint,
            "pipeline_fallback_enabled": self.pipeline_fallback_enabled,
            "ffmpeg": self.ffmpeg,
        }


def _check_backend() -> str | None:
    """Return 'sensevoice', 'faster-whisper', or None.

    Probes the import path actually used at runtime (AutoModel / WhisperModel),
    so missing transitive deps like torchaudio do not claim SenseVoice is ready.
    """
    try:
        from funasr import AutoModel  # noqa: F401

        return "sensevoice"
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel  # noqa: F401

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
        ffmpeg=_ffmpeg_available(),
    )


def _find_downloaded_audio(output_dir: Path) -> str:
    candidates = [path for path in output_dir.iterdir() if path.suffix.lower() in _AUDIO_EXTS]
    if not candidates:
        return ""
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _cookie_header_for_url(url: str) -> str:
    """Pick platform cookie header from settings when present."""
    from collectors.settings import settings

    host = (urlparse(url).hostname or "").lower()
    if "youtu" in host:
        return settings.youtube_cookie.strip()
    if "bilibili" in host:
        parts: list[str] = []
        if settings.bilibili_sessdata:
            parts.append(f"SESSDATA={settings.bilibili_sessdata.strip()}")
        if settings.bilibili_bili_jct:
            parts.append(f"bili_jct={settings.bilibili_bili_jct.strip()}")
        if settings.bilibili_dedeuserid:
            parts.append(f"DedeUserID={settings.bilibili_dedeuserid.strip()}")
        if settings.bilibili_buvid3:
            parts.append(f"buvid3={settings.bilibili_buvid3.strip()}")
        return "; ".join(parts)
    return ""


def _cookie_domain_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtu" in host:
        return ".youtube.com"
    if "bilibili" in host:
        return ".bilibili.com"
    return f".{host}" if host else ""


def _write_netscape_cookies(cookie_header: str, domain: str, path: Path) -> Path | None:
    """Write a Netscape cookie jar yt-dlp can consume. Returns path or None."""
    if not cookie_header or not domain:
        return None
    lines = ["# Netscape HTTP Cookie File", "# Generated by collectors.asr"]
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        # domain, include_subdomains, path, secure, expiry, name, value
        lines.append(f"{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}")
    if len(lines) <= 2:
        return None
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _clip_error(text: str, limit: int = 400) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _download_via_cli(url: str, output_dir: Path, *, cookies_file: Path | None = None) -> str:
    global _LAST_DOWNLOAD_ERROR
    cmd = [
        "yt-dlp",
        "-f",
        "bestaudio[ext=m4a]/bestaudio/best",
        "--no-playlist",
        "--output",
        str(output_dir / "%(id)s.%(ext)s"),
    ]
    if cookies_file and cookies_file.exists():
        cmd.extend(["--cookies", str(cookies_file)])
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        _LAST_DOWNLOAD_ERROR = _clip_error(result.stderr or result.stdout or "yt-dlp CLI failed")
        return ""
    path = _find_downloaded_audio(output_dir)
    if not path:
        _LAST_DOWNLOAD_ERROR = "yt-dlp finished but no audio file was found"
    return path


def _download_via_module(url: str, output_dir: Path, *, cookies_file: Path | None = None) -> str:
    global _LAST_DOWNLOAD_ERROR
    import yt_dlp

    outtmpl = str(output_dir / "%(id)s.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_file and cookies_file.exists():
        ydl_opts["cookiefile"] = str(cookies_file)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        _LAST_DOWNLOAD_ERROR = _clip_error(str(exc))
        return ""
    path = _find_downloaded_audio(output_dir)
    if not path:
        _LAST_DOWNLOAD_ERROR = "yt-dlp module finished but no audio file was found"
    return path


def download_audio(url: str, output_dir: Path) -> str:
    """Download audio via yt-dlp CLI or Python module. Returns path or ''."""
    global _LAST_DOWNLOAD_ERROR
    _LAST_DOWNLOAD_ERROR = ""
    output_dir.mkdir(parents=True, exist_ok=True)

    cookie_header = _cookie_header_for_url(url)
    domain = _cookie_domain_for_url(url)
    cookies_path: Path | None = None
    if cookie_header:
        cookies_path = _write_netscape_cookies(
            cookie_header,
            domain,
            output_dir / "yt_dlp_cookies.txt",
        )

    if _yt_dlp_cli_available():
        try:
            path = _download_via_cli(url, output_dir, cookies_file=cookies_path)
            if path:
                return path
        except subprocess.TimeoutExpired:
            _LAST_DOWNLOAD_ERROR = "yt-dlp timed out while downloading audio"
    if _yt_dlp_module_available():
        try:
            path = _download_via_module(url, output_dir, cookies_file=cookies_path)
            if path:
                return path
        except Exception as exc:
            _LAST_DOWNLOAD_ERROR = _clip_error(str(exc))
    if not _LAST_DOWNLOAD_ERROR:
        _LAST_DOWNLOAD_ERROR = "yt-dlp is not available"
    return ""


def last_download_error() -> str:
    return _LAST_DOWNLOAD_ERROR


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _max_audio_seconds() -> int:
    from collectors.settings import settings

    return max(30, int(getattr(settings, "asr_max_audio_seconds", 600) or 600))


def _convert_to_wav(audio_path: str, output_dir: Path | None = None) -> str:
    """Convert audio to 16k mono wav via ffmpeg when available. Returns wav path or ''."""
    if not _ffmpeg_available():
        return ""
    src = Path(audio_path)
    if not src.is_file():
        return ""
    dest_dir = output_dir or src.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    max_seconds = _max_audio_seconds()
    dest = dest_dir / f"{src.stem}_asr{max_seconds}s.wav"
    if src.suffix.lower() == ".wav" and src.stat().st_size < 2_000_000:
        # Already a short wav — reuse as-is.
        return str(src)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-t",
                str(max_seconds),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0 or not dest.is_file():
        return ""
    return str(dest)


def _prepare_audio_for_backend(audio_path: str, backend: str) -> str:
    """SenseVoice prefers wav; long videos are trimmed to asr_max_audio_seconds."""
    path = Path(audio_path)
    if backend == "sensevoice" or path.suffix.lower() not in {".wav", ".flac"}:
        converted = _convert_to_wav(str(path))
        if converted:
            return converted
    return str(path)


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


def _strip_sensevoice_tags(text: str) -> str:
    """Remove SenseVoice control tags such as <|en|> / <|Speech|>."""
    cleaned = re.sub(r"<\|[^|]*\|>", " ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


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
    return _strip_sensevoice_tags(" ".join(texts))


def transcribe_faster_whisper(audio_path: str, language: str | None = None) -> str:
    """Transcribe using faster-whisper. Returns raw text."""
    model = _get_faster_whisper_model()
    segments, _ = model.transcribe(audio_path, language=language, beam_size=5)
    return " ".join(seg.text for seg in segments).strip()


def transcribe_file(
    audio_path: str | Path,
    *,
    language: str = "auto",
) -> AsrResult:
    """Transcribe an existing local audio file (no yt-dlp)."""
    path = Path(audio_path)
    backend = _check_backend()
    if backend is None:
        readiness = check_readiness()
        return AsrResult(
            text="",
            backend="none",
            error=(
                "No ASR backend available. "
                + (readiness.install_hint or "Install funasr or faster-whisper.")
            ),
        )
    if not path.is_file():
        return AsrResult(text="", backend=backend, error=f"Audio file not found: {path}")

    prepared = _prepare_audio_for_backend(str(path), backend)
    try:
        if backend == "sensevoice":
            lang = language if language != "auto" else "auto"
            text = transcribe_sensevoice(prepared, language=lang)
        else:
            lang_fw = None if language == "auto" else language
            text = transcribe_faster_whisper(prepared, language=lang_fw)
    except Exception as primary_exc:
        # SenseVoice often fails on m4a/webm without a working ffmpeg path — fall back.
        if backend == "sensevoice":
            try:
                from faster_whisper import WhisperModel  # noqa: F401

                lang_fw = None if language == "auto" else language
                text = transcribe_faster_whisper(str(path), language=lang_fw)
                return AsrResult(
                    text=text,
                    backend="faster-whisper",
                    audio_path=str(path),
                    error="" if text else f"sensevoice failed ({primary_exc}); faster-whisper returned empty",
                )
            except Exception as fallback_exc:
                return AsrResult(
                    text="",
                    backend=backend,
                    audio_path=str(path),
                    error=f"{primary_exc}; faster-whisper fallback also failed: {fallback_exc}",
                )
        return AsrResult(text="", backend=backend, audio_path=str(path), error=str(primary_exc))

    return AsrResult(text=text, backend=backend, audio_path=prepared)


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
        detail = last_download_error() or "unknown yt-dlp error"
        cookie_hint = ""
        host = (urlparse(url).hostname or "").lower()
        if "youtu" in host:
            cookie_hint = " Set YOUTUBE_COOKIE if YouTube asks to sign in / confirm you are not a bot."
        elif "bilibili" in host:
            cookie_hint = " Set Bilibili SESSDATA cookies if the video requires login."
        return AsrResult(
            text="",
            backend=backend,
            error=f"yt-dlp failed to download audio from {url}: {detail}.{cookie_hint}",
        )

    return transcribe_file(audio_path, language=language)
