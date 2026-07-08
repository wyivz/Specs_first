"""In-page embedded browser control (Milestone 2 tail item).

Playwright's headed-fallback captcha solver (see ``collectors.browser``)
used to require the user to alt-tab into a separate OS window to click
through a slider/verification challenge. This module lets that same live
page be *driven remotely* from the web UI instead: the browser-side worker
publishes screenshots into a ``BrowserBridge`` and drains queued
click/type/key commands from it on every poll tick, while the frontend
(Streamlit panel or a REST client) only ever needs to render an image and
forward coordinates/text — no separate window to hunt for.

This module has no Playwright dependency itself; it is a thread-safe
mailbox that both sides poll, so it is fully unit-testable without a real
browser.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrowserCommand:
    action: str  # "click" | "type" | "key" | "scroll"
    kwargs: dict = field(default_factory=dict)


class BrowserBridge:
    """Thread-safe handoff between a live Playwright page and a UI client.

    The browser-side worker thread calls ``publish_screenshot`` and
    ``drain_commands`` on a tight loop. The UI thread calls
    ``latest_screenshot`` and ``submit_command``. Neither side blocks the
    other; screenshots are just the most-recent frame (not a queue).
    """

    def __init__(self, task_id: str, url: str = "") -> None:
        self.task_id = task_id
        self.url = url
        self.created_at = time.time()
        self._lock = threading.Lock()
        self._screenshot: bytes | None = None
        self._screenshot_seq = 0
        self._commands: list[BrowserCommand] = []
        self._solved = threading.Event()
        self._closed = threading.Event()
        self._error = ""

    # --- browser-side (worker thread) API -------------------------------
    def publish_screenshot(self, data: bytes) -> None:
        with self._lock:
            self._screenshot = data
            self._screenshot_seq += 1

    def drain_commands(self) -> list[BrowserCommand]:
        with self._lock:
            pending, self._commands = self._commands, []
        return pending

    def mark_solved(self) -> None:
        self._solved.set()

    def mark_error(self, message: str) -> None:
        self._error = message

    def close(self) -> None:
        self._closed.set()

    # --- UI-side API ------------------------------------------------------
    def submit_command(self, action: str, **kwargs) -> None:
        with self._lock:
            self._commands.append(BrowserCommand(action=action, kwargs=kwargs))

    def latest_screenshot(self) -> bytes | None:
        with self._lock:
            return self._screenshot

    @property
    def screenshot_seq(self) -> int:
        with self._lock:
            return self._screenshot_seq

    @property
    def is_solved(self) -> bool:
        return self._solved.is_set()

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    @property
    def error(self) -> str:
        return self._error


_BRIDGES: dict[str, BrowserBridge] = {}
_REGISTRY_LOCK = threading.Lock()


def get_or_create_bridge(task_id: str, url: str = "") -> BrowserBridge:
    with _REGISTRY_LOCK:
        bridge = _BRIDGES.get(task_id)
        if bridge is None or bridge.is_closed:
            bridge = BrowserBridge(task_id, url=url)
            _BRIDGES[task_id] = bridge
        return bridge


def get_bridge(task_id: str) -> BrowserBridge | None:
    with _REGISTRY_LOCK:
        return _BRIDGES.get(task_id)


def remove_bridge(task_id: str) -> None:
    with _REGISTRY_LOCK:
        bridge = _BRIDGES.pop(task_id, None)
    if bridge:
        bridge.close()
