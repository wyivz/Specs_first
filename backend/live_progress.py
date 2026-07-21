"""Live step progress helpers for pipeline → Streamlit status UI.

Events use ``event_type="step_status"`` with payload keys:
  action, url, url_label, started_at, sku, phase, highlights, detail
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlparse

StepEmitter = Callable[[str, dict[str, Any]], None]

_step_emitter: ContextVar[StepEmitter | None] = ContextVar("specs_step_emitter", default=None)

_PLATFORM_HOSTS: tuple[tuple[str, str], ...] = (
    ("item.jd.com", "京东"),
    ("jd.com", "京东"),
    ("tmall.com", "天猫"),
    ("taobao.com", "淘宝"),
    ("bilibili.com", "B站"),
    ("youtube.com", "YouTube"),
    ("youtu.be", "YouTube"),
    ("reddit.com", "Reddit"),
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def platform_label_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for needle, label in _PLATFORM_HOSTS:
        if host == needle or host.endswith("." + needle):
            return label
    return host or "网页"


def short_url_label(url: str, *, max_len: int = 48) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    label = f"{platform_label_from_url(url)}{path}"
    if len(label) <= max_len:
        return label
    return label[: max_len - 1] + "…"


def extract_url(text: str) -> str:
    for token in (text or "").split():
        if token.startswith("http://") or token.startswith("https://"):
            return token.rstrip(".,;）)]>")
    return ""


def set_step_emitter(emitter: StepEmitter | None):
    return _step_emitter.set(emitter)


def reset_step_emitter(token) -> None:
    _step_emitter.reset(token)


def emit_live_step(
    action: str,
    *,
    sku: str = "",
    phase: int | None = None,
    url: str = "",
    url_label: str = "",
    detail: str = "",
    highlights: list[str] | None = None,
    started_at: str | None = None,
) -> None:
    """Push a step_status payload via the active pipeline emitter (no-op if unset)."""
    emitter = _step_emitter.get()
    if emitter is None:
        return
    label = url_label or (short_url_label(url) if url else "")
    payload: dict[str, Any] = {
        "action": action,
        "sku": sku,
        "url": url,
        "url_label": label,
        "detail": detail,
        "highlights": list(highlights or []),
        "started_at": started_at or now_iso(),
    }
    if phase is not None:
        payload["phase"] = phase
        payload["phase_label"] = action
    emitter(action, payload)


def format_fetch_action(url: str, *, verb: str = "抓取") -> str:
    platform = platform_label_from_url(url)
    return f"{verb}{platform}商品信息中"
