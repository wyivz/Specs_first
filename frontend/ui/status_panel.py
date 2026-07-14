from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.api_client import get_api_client
from frontend.event_listener import drain_events, ensure_listener, sync_events_from_snapshot
from frontend.state import apply_event, compute_progress_value
from frontend.ui.browser_panel import render_embedded_browser_panel


def render_health_bar() -> None:
    try:
        health = get_api_client().health()
    except Exception as exc:
        st.error(f"Health 检查失败：{exc}")
        return

    status = health.get("status", "unknown")
    badge = {"ok": "🟢", "degraded": "🟡", "error": "🔴"}.get(status, "⚪")
    st.caption(f"{badge} 服务状态：**{status}** · {health.get('checked_at', '')}")

    checks = health.get("checks") or []
    if checks:
        labels = []
        for item in checks[:8]:
            level = {"ok": "🟢", "degraded": "🟡", "error": "🔴"}.get(item.get("status", ""), "⚪")
            labels.append(f"{level} {item.get('name', '?')}")
        st.caption(" · ".join(labels))


def _sync_task_events(task_id: str) -> list[dict[str, Any]]:
    new_events = drain_events(task_id)
    if new_events:
        return new_events

    api = get_api_client()
    snapshot = api.events_snapshot(task_id)
    return sync_events_from_snapshot(task_id, snapshot)


def _render_progress(status: dict[str, Any], progress_info: dict[str, Any], new_events: list[dict[str, Any]]) -> None:
    total_steps = st.session_state.get("total_steps", 1)
    progress_value = compute_progress_value(status["state"], progress_info, total_steps)
    progress_text = new_events[-1]["message"] if new_events else progress_info.get("phase_label") or "Running..."
    st.progress(progress_value, text=progress_text)

    total_skus = max(int(progress_info.get("total_skus") or total_steps), 1)
    phase = int(progress_info.get("phase") or 0)
    sku_index = int(progress_info.get("sku_index") or 0)
    sku = progress_info.get("sku") or "—"
    phase_label = progress_info.get("phase_label") or "准备中"
    category = progress_info.get("category") or ""
    st.markdown(
        f"**当前 SKU** `{sku}` · 第 **{sku_index + 1}/{total_skus}** 个"
        + (f" · 品类 `{category}`" if category else "")
        + f" · **{phase_label}**"
    )

    profile = st.session_state.get("category_profile")
    if profile:
        slots = profile.get("slots") or []
        keywords = profile.get("comparison_keywords") or []
        source = profile.get("source") or profile.get("category_label") or ""
        label = profile.get("category") or profile.get("category_label") or category
        st.caption(
            f"JIT Schema · **{label}**（{source}）· 硬指标：{', '.join(slots) or '—'} · "
            f"对比关键词：{', '.join(keywords) or '—'}"
        )

    step_names = ["发现", "规格", "口碑", "价格", "仲裁"]
    step_cols = st.columns(len(step_names))
    active_step = 0 if phase <= 0 else min(phase, 4)
    for idx, name in enumerate(step_names):
        if idx < active_step:
            step_cols[idx].success(name)
        elif idx == active_step and status["state"] == "RUNNING":
            step_cols[idx].info(f"▶ {name}")
        else:
            step_cols[idx].caption(name)


def _render_diagnostics() -> None:
    diagnostics = st.session_state.get("diagnostics", [])
    if not diagnostics:
        return
    with st.expander("采集诊断 / 降级日志", expanded=any(item.get("level") == "error" for item in diagnostics)):
        for item in diagnostics[-40:]:
            st.markdown(
                f"- `{item.get('level', 'info')}` **{item.get('source')}** "
                f"({item.get('sku', 'all')}): {item.get('message')}"
            )


def _handle_terminal_state(task_id: str, status: dict[str, Any]) -> None:
    api = get_api_client()
    state = status["state"]

    if state == "DONE":
        st.session_state["result"] = api.get_result(task_id)
        st.success("Obsidian assets written.")
    elif state == "PAUSED_NEED_AUTH":
        st.session_state["paused_task_id"] = task_id
        st.warning(
            "检测到验证码/安全检测，任务已挂起。"
            "淘宝/天猫请优先在弹出的 Chrome/Edge 窗口拖动滑块，或更新 TAOBAO_COOKIE 后续传；"
            "其他站点可在侧边栏点击「续传任务」。"
        )
        render_embedded_browser_panel(task_id)
    elif state == "FAILED":
        st.error(f"任务失败：{status.get('error') or st.session_state.get('task_error', '')}")

    st.session_state.pop("active_task_id", None)
    st.rerun()


def _tick_active_task(task_id: str) -> None:
    api = get_api_client()
    status = api.get_task(task_id)
    new_events = _sync_task_events(task_id)

    for event in new_events:
        apply_event(event)

    progress_info = st.session_state.get("progress_info", {})
    _render_progress(status, progress_info, new_events)

    with st.expander("实时事件流", expanded=True):
        st.markdown("\n".join(st.session_state.get("events_log", [])[-16:]) or "_暂无事件_")

    _render_diagnostics()
    render_embedded_browser_panel(task_id)

    if status["state"] in {"DONE", "FAILED", "PAUSED_NEED_AUTH"}:
        _handle_terminal_state(task_id, status)


@st.fragment(run_every=timedelta(seconds=1))
def live_status_fragment() -> None:
    task_id = st.session_state.get("active_task_id")
    if not task_id:
        return

    ensure_listener(task_id)

    st.subheader("运行状态")
    render_health_bar()
    _tick_active_task(task_id)


def render_status_panel_idle() -> None:
    st.subheader("运行状态")
    render_health_bar()
    task_id = st.session_state.get("paused_task_id")
    if task_id:
        st.info(f"任务 `{task_id}` 已挂起，等待验证码处理。请在侧边栏点击「续传任务」。")
        render_embedded_browser_panel(task_id)
    elif st.session_state.get("task_error"):
        st.error(f"上次任务失败：{st.session_state['task_error']}")
    else:
        st.caption("无运行中任务。填写输入区并点击「开始对比」。")
