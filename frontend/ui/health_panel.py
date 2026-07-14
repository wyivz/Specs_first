from __future__ import annotations

import html
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.api_client import get_api_client

CHECK_HINTS: dict[str, str] = {
    "gemini_model": "在 .env 配置 GEMINI_API_KEY",
    "openai_api": "在 .env 配置 OPENAI_API_KEY",
    "jd_cookie": "在 .env 配置 JD_COOKIE",
    "taobao_cookie": "在 .env 配置 TAOBAO_COOKIE 或 _m_h5_tk",
    "bilibili_cookie": "在 .env 配置 B 站 SESSDATA / bili_jct 等",
    "youtube_cookie": "YouTube 可选；部分视频需 Cookie",
    "playwright": "运行 playwright install chromium",
}


def refresh_health_cache() -> dict[str, Any]:
    health = get_api_client().health()
    st.session_state["health_cache"] = health
    return health


def get_cached_health(*, force: bool = False) -> dict[str, Any]:
    if force or "health_cache" not in st.session_state:
        return refresh_health_cache()
    return st.session_state["health_cache"]


def real_mode_ready(health: dict[str, Any] | None = None) -> tuple[bool, str]:
    health = health or get_cached_health()
    overall = health.get("status", "unknown")
    if overall == "error":
        failed = [c.get("name", "?") for c in health.get("checks", []) if c.get("status") == "error"]
        names = "、".join(failed[:4]) or "关键配置"
        return False, f"Real 模式不可用：{names} 未就绪。请修复 Health 检查或先用 Mock 演示。"
    return True, ""


def render_health_panel(*, compact: bool = False) -> dict[str, Any]:
    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.markdown("**平台就绪状态**")
    with col_btn:
        if st.button("刷新", key="health_refresh", use_container_width=True):
            refresh_health_cache()
            st.rerun()

    health = get_cached_health()
    overall = health.get("status", "unknown")
    pill_class = {"ok": "sf-pill-ok", "degraded": "sf-pill-warn", "error": "sf-pill-error"}.get(overall, "")
    overall_label = {"ok": "就绪", "degraded": "部分就绪", "error": "未就绪"}.get(overall, overall)

    if compact:
        st.markdown(
            f'<span class="sf-pill {pill_class}">{overall_label}</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="sf-badge-row"><span class="sf-pill {pill_class}">总状态 · {overall_label}</span></div>',
            unsafe_allow_html=True,
        )

    checks = health.get("checks") or []
    if not checks:
        return health

    if compact:
        return health

    cols = st.columns(min(len(checks), 4))
    for idx, item in enumerate(checks[:8]):
        status = item.get("status", "skip")
        css = {"ok": "sf-pill-ok", "degraded": "sf-pill-warn", "warn": "sf-pill-warn", "error": "sf-pill-error"}.get(
            status, ""
        )
        name = item.get("name", "?")
        hint = html.escape(CHECK_HINTS.get(name, item.get("message", "")))
        with cols[idx % len(cols)]:
            st.markdown(f'<span class="sf-pill {css}" title="{hint}">{name}</span>', unsafe_allow_html=True)
            if status in {"error", "warn", "degraded"} and hint:
                st.caption(hint[:80])

    return health
