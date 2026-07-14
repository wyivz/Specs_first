from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from collectors.embedded_browser import get_bridge
from frontend.api_client import get_api_client
from frontend.event_listener import drain_events, ensure_listener, sync_events_from_snapshot
from frontend.state import apply_event, compute_progress_value
from frontend.ui.browser_panel import render_embedded_browser_panel
from frontend.ui.matrix import render_evidence_cards, render_matrix_header, render_matrix_table
from frontend.ui.labels import build_column_labels


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
    progress_text = new_events[-1]["message"] if new_events else progress_info.get("phase_label") or "运行中…"
    st.progress(progress_value, text=progress_text)

    total_skus = max(int(progress_info.get("total_skus") or total_steps), 1)
    phase = int(progress_info.get("phase") or 0)
    sku_index = int(progress_info.get("sku_index") or 0)
    sku = progress_info.get("sku") or "—"
    phase_label = progress_info.get("phase_label") or "准备中"
    category = progress_info.get("category") or ""
    st.markdown(
        f"**SKU** `{sku}` · **{sku_index + 1}/{total_skus}** · **{phase_label}**"
        + (f" · 品类 `{category}`" if category else "")
    )

    profile = st.session_state.get("category_profile")
    if profile:
        slots = profile.get("slots") or []
        label_map = build_column_labels(profile)
        slot_labels = [label_map.get(s, s) for s in slots[:6]]
        st.caption(f"对比维度：{', '.join(slot_labels) or '—'}")

    step_names = ["发现", "规格", "口碑", "价格", "仲裁"]
    step_html = []
    active_step = 0 if phase <= 0 else min(phase, 4)
    for idx, name in enumerate(step_names):
        if idx < active_step:
            step_html.append(f'<span class="sf-pill sf-pill-ok">{name} ✓</span>')
        elif idx == active_step and status["state"] == "RUNNING":
            step_html.append(f'<span class="sf-pill sf-pill-live">▶ {name}</span>')
        else:
            step_html.append(f'<span class="sf-pill">{name}</span>')
    st.markdown(f'<div class="sf-badge-row">{"".join(step_html)}</div>', unsafe_allow_html=True)


def _render_diagnostics(*, expanded_on_error: bool = True) -> None:
    diagnostics = st.session_state.get("diagnostics", [])
    if not diagnostics:
        return
    has_error = any(item.get("level") == "error" for item in diagnostics)
    with st.expander(f"采集诊断（{len(diagnostics)} 条）", expanded=expanded_on_error and has_error):
        for item in diagnostics[-30:]:
            st.markdown(
                f"- `{item.get('level', 'info')}` **{item.get('source')}** "
                f"({item.get('sku', 'all')}): {item.get('message')}"
            )


def _render_event_log(new_events: list[dict[str, Any]]) -> None:
    has_new = bool(new_events)
    with st.expander("事件日志", expanded=has_new and any(e.get("event_type") == "sku_failed" for e in new_events)):
        st.markdown("\n".join(st.session_state.get("events_log", [])[-12:]) or "_暂无事件_")


def _handle_terminal_state(task_id: str, status: dict[str, Any]) -> None:
    api = get_api_client()
    state = status["state"]

    if state == "DONE":
        st.session_state["result"] = api.get_result(task_id)
        st.session_state["task_completed"] = True
    elif state == "PAUSED_NEED_AUTH":
        st.session_state["paused_task_id"] = task_id
    elif state == "FAILED":
        st.session_state["task_error"] = status.get("error") or st.session_state.get("task_error", "")

    st.session_state.pop("active_task_id", None)
    st.rerun()


def _should_show_browser(task_id: str, status: dict[str, Any]) -> bool:
    if status["state"] == "PAUSED_NEED_AUTH":
        return True
    return get_bridge(task_id) is not None


@st.fragment(run_every=timedelta(seconds=1))
def live_workspace_fragment() -> None:
    """Live status (left) + progressive matrix (right) while task runs."""
    task_id = st.session_state.get("active_task_id")
    if not task_id:
        return

    ensure_listener(task_id)

    api = get_api_client()
    status = api.get_task(task_id)
    new_events = _sync_task_events(task_id)
    for event in new_events:
        apply_event(event)

    progress_info = st.session_state.get("progress_info", {})
    matrix_rows = st.session_state.get("matrix_rows", [])
    profile = st.session_state.get("category_profile")
    total_expected = max(int(progress_info.get("total_skus") or st.session_state.get("total_steps", 1)), 1)

    st.subheader("运行中")
    col_status, col_matrix = st.columns([2, 3], gap="large")

    with col_status:
        _render_progress(status, progress_info, new_events)
        _render_event_log(new_events)
        _render_diagnostics()
        if _should_show_browser(task_id, status):
            render_embedded_browser_panel(task_id)

    with col_matrix:
        st.markdown("**对比矩阵**")
        render_matrix_header(len(matrix_rows), total_expected, live=True)
        render_matrix_table(matrix_rows, profile=profile)
        render_evidence_cards(matrix_rows, expanded_only=True)

    if status["state"] in {"DONE", "FAILED", "PAUSED_NEED_AUTH"}:
        if status["state"] == "PAUSED_NEED_AUTH":
            st.warning(
                "任务已挂起：请在弹出浏览器或侧边栏完成验证后点击「续传任务」。"
            )
        elif status["state"] == "FAILED":
            st.error(f"任务失败：{status.get('error') or st.session_state.get('task_error', '')}")
        _handle_terminal_state(task_id, status)


def render_paused_panel() -> None:
    task_id = st.session_state.get("paused_task_id")
    if not task_id:
        return
    st.subheader("等待验证")
    st.warning(f"任务 `{task_id}` 已挂起。请在侧边栏点击「续传任务」。")
    render_embedded_browser_panel(task_id)


def render_idle_status() -> None:
    if st.session_state.get("task_error"):
        st.error(f"上次任务失败：{st.session_state['task_error']}")
    elif st.session_state.get("task_completed"):
        st.success("上次对比已完成，结果见下方输出区。")
    else:
        st.caption("填写对比意图后点击「开始对比」。Mock 模式无需配置即可体验。")
