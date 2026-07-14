"""Persist short-lived session tokens across runs (not secrets vault — local cache only)."""

from __future__ import annotations

import json
from pathlib import Path


def _taobao_token_path() -> Path:
    from collectors.settings import settings

    return Path(settings.vault_path) / "taobao_m_h5_tk.cache"


def save_taobao_m_h5_tk(token: str) -> None:
    value = (token or "").strip()
    if not value:
        return
    path = _taobao_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def load_taobao_m_h5_tk() -> str:
    path = _taobao_token_path()
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def extract_m_h5_tk_from_storage_state(storage_state_path: str | Path | None) -> str:
    """Pull `_m_h5_tk` from a Playwright storage_state JSON if present."""
    if not storage_state_path:
        return ""
    path = Path(storage_state_path)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    cookies = payload.get("cookies") if isinstance(payload, dict) else None
    if not isinstance(cookies, list):
        return ""
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if name == "_m_h5_tk":
            return str(item.get("value", "")).strip()
    return ""


def sync_taobao_token_from_storage_state(storage_state_path: str | Path | None) -> str:
    token = extract_m_h5_tk_from_storage_state(storage_state_path)
    if token:
        save_taobao_m_h5_tk(token)
    return token
