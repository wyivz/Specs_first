from __future__ import annotations

import gzip
import zlib


def decompress_http_body(raw: bytes, content_encoding: str = "") -> tuple[bytes, str]:
    """Decode Content-Encoding (gzip/deflate/br) and magic-byte compressed bodies.

    Returns (bytes, note) where note is empty on identity, or a short codec label.
    Never raises: on failure returns the original bytes with note describing the miss.
    """
    if not raw:
        return raw, ""
    encoding = (content_encoding or "").split(",")[0].strip().lower()
    data = raw
    note = ""

    try:
        if encoding in {"gzip", "x-gzip"} or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
            note = "gzip"
        elif encoding == "deflate":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
            note = "deflate"
        elif encoding in {"br", "brotli"} or _looks_like_brotli(data):
            data, ok = _try_brotli(data)
            note = "brotli" if ok else "brotli-missing"
            if not ok:
                return raw, note
    except Exception:
        return raw, f"{encoding or 'compressed'}-failed"

    return data, note


def looks_like_compressed_or_binary(data: bytes) -> bool:
    if not data:
        return False
    if data[:2] == b"\x1f\x8b":
        return True
    if _looks_like_brotli(data):
        return True
    sample = data[:512]
    if not sample:
        return False
    # High NUL / control ratio → not HTML/JSON text.
    control = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 0)
    return control / len(sample) > 0.08


def decode_http_text(raw: bytes, charset: str = "utf-8", content_encoding: str = "") -> tuple[str, str]:
    """Decompress then charset-decode. Returns (text, decode_note)."""
    data, note = decompress_http_body(raw, content_encoding)
    if looks_like_compressed_or_binary(data):
        return "", note or "binary"
    encoding = charset or "utf-8"
    try:
        text = data.decode(encoding, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    if text.count("\ufffd") > max(20, len(text) // 20):
        return "", (note + "+mojibake").strip("+")
    return text, note


def _try_brotli(data: bytes) -> tuple[bytes, bool]:
    for module_name in ("brotli", "brotlicffi"):
        try:
            module = __import__(module_name)
            return module.decompress(data), True
        except Exception:
            continue
    return data, False


def _looks_like_brotli(data: bytes) -> bool:
    # Common brotli window header seen from CN CDNs when Accept-Encoding includes br,
    # or when servers ignore Accept-Encoding and still send br.
    if len(data) < 4:
        return False
    if data[0] == 0xCE and data[1] == 0x08:
        return True
    # Replacement-char path already decoded wrongly — callers pass raw bytes only.
    return data[0] in {0x0B, 0x1B, 0x8B} and data[1:3] in {b"\x00\x00", b"\x08\x00"}
