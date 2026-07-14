"""Backward-compatible shim — use frontend.ui.live_workspace instead."""

from frontend.ui.live_workspace import (
    live_workspace_fragment,
    render_idle_status,
    render_paused_panel,
)

live_status_fragment = live_workspace_fragment


def render_status_panel_idle() -> None:
    render_idle_status()

__all__ = [
    "live_status_fragment",
    "live_workspace_fragment",
    "render_status_panel_idle",
    "render_idle_status",
    "render_paused_panel",
]
