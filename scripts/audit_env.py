#!/usr/bin/env python3
"""Audit local .env against .env.example without printing secret values."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.credentials import extract_m_h5_tk, parse_cookie_header  # noqa: E402


def load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def main() -> int:
    example = load_env(ROOT / ".env.example")
    actual = load_env(ROOT / ".env")
    if not actual:
        print("ERROR: .env not found or empty")
        return 1

    print("=== .env audit (values hidden) ===")
    missing = [k for k in sorted(example) if k not in actual]
    empty = [k for k in sorted(example) if k in actual and not actual[k]]
    present = [k for k in sorted(example) if k in actual and actual[k]]
    extra = sorted(set(actual) - set(example))
    print(f"present ({len(present)}): {', '.join(present)}")
    print(f"empty ({len(empty)}): {', '.join(empty)}")
    print(f"missing ({len(missing)}): {', '.join(missing)}")
    if extra:
        print(f"extra keys ({len(extra)}): {', '.join(extra)}")

    print()
    print("=== key readiness ===")
    mode = actual.get("SPECS_FIRST_MODE", "")
    print(f"[{'OK' if mode == 'real' else 'WARN':7}] SPECS_FIRST_MODE: {mode or '(empty)'}")

    cookie_hints = {
        "JD_COOKIE": (".jd.com", ("pt_key", "pt_pin")),
        "TAOBAO_COOKIE": (".taobao.com", ("_m_h5_tk", "cookie2")),
        "YOUTUBE_COOKIE": (".youtube.com", ("VISITOR_INFO1_LIVE",)),
        "REDDIT_COOKIE": (".reddit.com", ("reddit_session",)),
    }
    for key in (
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "JD_COOKIE",
        "TAOBAO_COOKIE",
        "TAOBAO_M_H5_TK",
        "BILIBILI_SESSDATA",
        "BILIBILI_BILI_JCT",
        "BILIBILI_DEDEUSERID",
        "YOUTUBE_COOKIE",
        "REDDIT_COOKIE",
    ):
        value = actual.get(key, "")
        if not value:
            print(f"[MISSING] {key}: empty")
            continue
        strength = "OK" if len(value) >= 20 else "WEAK"
        hint_text = ""
        spec = cookie_hints.get(key)
        if spec:
            domain, names = spec
            cookie_names = {item["name"] for item in parse_cookie_header(value, domain)}
            parts = [f"{name}={'yes' if name in cookie_names else 'no'}" for name in names]
            hint_text = f" ({', '.join(parts)})"
        print(f"[{strength:7}] {key}: set ({len(value)} chars){hint_text}")

    taobao_cookie = actual.get("TAOBAO_COOKIE", "")
    taobao_token = actual.get("TAOBAO_M_H5_TK", "") or extract_m_h5_tk(taobao_cookie)
    print(
        f"[{'OK' if taobao_token else 'WARN':7}] Taobao sign token: "
        f"{'derived from cookie' if taobao_token and not actual.get('TAOBAO_M_H5_TK') else 'set' if taobao_token else 'missing (_m_h5_tk)'}"
    )

    jd_cookie = actual.get("JD_COOKIE", "")
    if jd_cookie:
        jd_names = {item["name"] for item in parse_cookie_header(jd_cookie, ".jd.com")}
        login_ok = "pt_key" in jd_names and "pt_pin" in jd_names
        print(
            f"[{'OK' if login_ok else 'WARN':7}] JD login session: "
            f"{'pt_key/pt_pin present' if login_ok else 'missing pt_key/pt_pin — refresh JD_COOKIE from logged-in jd.com'}"
        )

    gemini_model = actual.get("DEFAULT_GEMINI_MODEL", example.get("DEFAULT_GEMINI_MODEL", ""))
    print(f"[{'OK' if gemini_model else 'WARN':7}] DEFAULT_GEMINI_MODEL: {gemini_model or '(default)'}")
    parallel = actual.get("COLLECTION_PARALLEL_PLATFORMS", "")
    print(f"[{'OK' if parallel.lower() == 'true' else 'INFO':7}] COLLECTION_PARALLEL_PLATFORMS: {parallel or '(default)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
