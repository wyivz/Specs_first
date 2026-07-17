"""Smoke-test local ASR readiness and optional one-shot transcription.

Usage:
  python scripts/smoke_asr.py
  python scripts/smoke_asr.py --self-test
  python scripts/smoke_asr.py --url https://www.youtube.com/watch?v=jNQXAC9IVRw
  python scripts/smoke_asr.py --url https://www.bilibili.com/video/BVxxxx --language zh
  python scripts/smoke_asr.py --file path/to/audio.wav
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_tone_wav(path: Path, *, seconds: float = 1.0, freq: float = 440.0) -> Path:
    """Write a short mono PCM wav so SenseVoice/faster-whisper can load without a video URL."""
    import math

    sample_rate = 16000
    n_samples = int(sample_rate * seconds)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            # Quiet tone — model may return empty text; success = no exception.
            value = int(4000 * math.sin(2 * math.pi * freq * (i / sample_rate)))
            frames.extend(struct.pack("<h", value))
        wf.writeframes(bytes(frames))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test local ASR stack")
    parser.add_argument("--url", default="", help="Optional video URL to transcribe")
    parser.add_argument("--file", default="", help="Optional local audio file to transcribe")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Load ASR backend on a synthetic wav (no network download)",
    )
    parser.add_argument("--language", default="auto", choices=["auto", "zh", "en"])
    parser.add_argument(
        "--output-dir",
        default="vault_output/asr_cache",
        help="Directory for downloaded audio",
    )
    args = parser.parse_args()

    from collectors.asr import check_readiness, transcribe_file, transcribe_url
    from collectors.settings import settings

    readiness = check_readiness()
    print(json.dumps(readiness.to_dict(), ensure_ascii=False, indent=2))
    if not readiness.ready and not args.file and not args.self_test:
        print("ASR stack not ready.", file=sys.stderr)
        if readiness.install_hint:
            print(f"Hint: {readiness.install_hint}", file=sys.stderr)
        return 1

    print(
        f"pipeline fallbacks: bilibili={settings.bilibili_asr_fallback} "
        f"youtube={settings.youtube_asr_fallback}"
    )

    if args.self_test:
        tone = _write_tone_wav(Path(args.output_dir) / "self_test_tone.wav")
        print(f"self-test audio: {tone}")
        result = transcribe_file(tone, language=args.language)
        payload = {
            "ok": result.error == "",
            "backend": result.backend,
            "audio_path": result.audio_path,
            "char_count": len(result.text),
            "error": result.error,
            "text_preview": result.text[:500],
            "note": "empty text on a pure tone is OK; failure means model load/transcribe crashed",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.error == "" else 2

    if args.file.strip():
        result = transcribe_file(args.file.strip(), language=args.language)
        payload = {
            "ok": result.ok or (result.error == "" and result.text == ""),
            "backend": result.backend,
            "audio_path": result.audio_path,
            "char_count": len(result.text),
            "error": result.error,
            "text_preview": result.text[:500],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.error == "" else 2

    if not args.url.strip():
        default = settings.smoke_youtube_url.strip()
        print(f"No --url/--file/--self-test given; readiness OK. Example: --url {default}")
        print("Or: python scripts/smoke_asr.py --self-test")
        return 0

    print(f"transcribing {args.url} with language={args.language} …")
    result = transcribe_url(
        args.url.strip(),
        output_dir=Path(args.output_dir),
        language=args.language,
    )
    payload = {
        "ok": result.ok,
        "backend": result.backend,
        "audio_path": result.audio_path,
        "char_count": len(result.text),
        "error": result.error,
        "text_preview": result.text[:500],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
