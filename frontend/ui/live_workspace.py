from __future__ import annotations

from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from collectors.embedded_browser import get_bridge
from frontend.event_listener import drain_events, ensure_listener, stop_listener
from frontend.live_data import events_since, get_task_result, get_task_status
from frontend.state import apply_event, compute_progress_value
from frontend.ui.browser_panel import render_embedded_browser_panel
from frontend.ui.matrix import render_evidence_cards, render_matrix_header, render_matrix_table
from frontend.ui.labels import build_column_labels

_TERMINAL_STATES = frozenset({"DONE", "FAILED", "PAUSED_NEED_AUTH"})
# Fragment polls: keep sub-2s so "已运行 Xs" ticks while long fetches run.
_LIVE_POLL_SECONDS = 1.5


def _elapsed_seconds(started_at: str) -> int:
    if not started_at:
        return 0
    try:
        from datetime import UTC, datetime

        raw = started_at.replace("Z", "+00:00")
        started = datetime.fromisoformat(raw)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return max(0, int((datetime.now(UTC) - started).total_seconds()))
    except Exception:
        return 0


def _format_live_action(progress_info: dict[str, Any]) -> str:
    action = str(progress_info.get("action") or progress_info.get("phase_label") or "运行中…")
    url = str(progress_info.get("url") or "")
    url_label = str(progress_info.get("url_label") or url)
    elapsed = _elapsed_seconds(str(progress_info.get("started_at") or ""))
    link_bit = ""
    if url:
        safe_label = url_label.replace("]", "")
        link_bit = f" [{safe_label}]({url})"
    detail = str(progress_info.get("detail") or "").strip()
    detail_bit = f" · {detail}" if detail else ""
    return f"{action}{link_bit}{detail_bit}，已运行 **{elapsed}** 秒"


def _sync_task_events(task_id: str) -> list[dict[str, Any]]:
    """Drain the listener queue, then advance UI from an in-process event delta."""
    drain_events(task_id)
    seen = int(st.session_state.get("seen_event_count", 0) or 0)
    new_events, total = events_since(task_id, seen)
    st.session_state["seen_event_count"] = total
    return new_events


def _resolve_status(api_status: dict[str, Any], new_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer API state; if events already signalled terminal, don't wait a tick."""
    state = api_status.get("state") or "RUNNING"
    if state in _TERMINAL_STATES:
        return api_status

    for event in reversed(new_events):
        event_type = event.get("event_type")
        if event_type == "task_done":
            return {**api_status, "state": "DONE"}
        if event_type == "task_failed":
            return {
                **api_status,
                "state": "FAILED",
                "error": (event.get("payload") or {}).get("error") or event.get("message") or api_status.get("error"),
            }
        if event_type == "auth_required":
            return {**api_status, "state": "PAUSED_NEED_AUTH"}
    return api_status


def _live_fingerprint(
    task_id: str,
    status: dict[str, Any],
    progress_info: dict[str, Any],
    matrix_rows: list[dict[str, Any]],
    new_event_count: int,
) -> tuple[Any, ...]:
    bridge = get_bridge(task_id)
    shot_seq = bridge.screenshot_seq if bridge else 0
    return (
        status.get("state"),
        status.get("error") or "",
        int(st.session_state.get("seen_event_count", 0) or 0),
        new_event_count,
        len(matrix_rows),
        progress_info.get("phase"),
        progress_info.get("sku_index"),
        progress_info.get("phase_label"),
        progress_info.get("progress"),
        progress_info.get("sku"),
        progress_info.get("action"),
        progress_info.get("url"),
        progress_info.get("started_at"),
        tuple(progress_info.get("highlights") or []),
        _elapsed_seconds(str(progress_info.get("started_at") or "")),
        shot_seq,
    )


def _render_progress(status: dict[str, Any], progress_info: dict[str, Any], new_events: list[dict[str, Any]]) -> None:
    total_steps = st.session_state.get("total_steps", 1)
    progress_value = compute_progress_value(status["state"], progress_info, total_steps)
    live_line = _format_live_action(progress_info)
    progress_text = live_line.replace("**", "")
    if new_events and not progress_info.get("action"):
        progress_text = str(new_events[-1].get("message") or progress_text)
    st.progress(progress_value, text=str(progress_text)[:220])

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
    st.markdown(live_line)

    highlights = list(progress_info.get("highlights") or [])
    history = list(st.session_state.get("live_step_history") or [])
    preview = highlights or history[-6:]
    if preview:
        st.markdown("**刚拿到的信息**")
        for item in preview[-6:]:
            st.markdown(f"- {item}")

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
    state = status["state"]

    if state == "DONE":
        st.session_state["result"] = get_task_result(task_id)
        st.session_state["task_completed"] = True
    elif state == "PAUSED_NEED_AUTH":
        st.session_state["paused_task_id"] = task_id
    elif state == "FAILED":
        st.session_state["task_error"] = status.get("error") or st.session_state.get("task_error", "")

    st.session_state.pop("active_task_id", None)
    st.session_state.pop("_live_fingerprint", None)
    st.rerun(scope="app")


def _should_show_browser(task_id: str, status: dict[str, Any]) -> bool:
    if status["state"] == "PAUSED_NEED_AUTH":
        return True
    return get_bridge(task_id) is not None


def _render_live_panels(
    task_id: str,
    status: dict[str, Any],
    progress_info: dict[str, Any],
    new_events: list[dict[str, Any]],
    *,
    show_details: bool,
) -> None:
    matrix_rows = st.session_state.get("matrix_rows", [])
    profile = st.session_state.get("category_profile")
    total_expected = max(int(progress_info.get("total_skus") or st.session_state.get("total_steps", 1)), 1)

    st.subheader("运行中")
    col_status, col_matrix = st.columns([2, 3], gap="large")

    with col_status:
        _render_progress(status, progress_info, new_events)
        if show_details:
            _render_event_log(new_events)
            _render_diagnostics()
        else:
            st.caption("状态未变 · 跳过日志重绘")
        if _should_show_browser(task_id, status):
            render_embedded_browser_panel(task_id)

    with col_matrix:
        st.markdown("**对比矩阵**")
        render_matrix_header(len(matrix_rows), total_expected, live=True)
        render_matrix_table(matrix_rows, profile=profile, dense=True)
        if show_details:
            render_evidence_cards(matrix_rows, expanded_only=True)


def _live_workspace_body() -> None:
    task_id = st.session_state.get("active_task_id")
    if not task_id:
        return

    ensure_listener(task_id)

    api_status = get_task_status(task_id)
    new_events = _sync_task_events(task_id)
    for event in new_events:
        apply_event(event)

    status = _resolve_status(api_status, new_events)

    if status["state"] in _TERMINAL_STATES:
        stop_listener(task_id)

    progress_info = st.session_state.get("progress_info", {})
    matrix_rows = st.session_state.get("matrix_rows", [])
    fingerprint = _live_fingerprint(task_id, status, progress_info, matrix_rows, len(new_events))
    previous = st.session_state.get("_live_fingerprint")
    changed = fingerprint != previous or status["state"] in _TERMINAL_STATES
    st.session_state["_live_fingerprint"] = fingerprint

    _render_live_panels(
        task_id,
        status,
        progress_info,
        new_events,
        show_details=changed,
    )

    if status["state"] in _TERMINAL_STATES:
        if status["state"] == "PAUSED_NEED_AUTH":
            st.warning(
                "任务已挂起：请在弹出浏览器或侧边栏完成验证后点击「续传任务」。"
            )
        elif status["state"] == "FAILED":
            st.error(f"任务失败：{status.get('error') or st.session_state.get('task_error', '')}")
        _handle_terminal_state(task_id, status)


@st.fragment(run_every=_LIVE_POLL_SECONDS)
def live_workspace_fragment() -> None:
    """Live status (left) + progressive matrix (right) while task runs."""
    try:
        _live_workspace_body()
    except Exception as exc:  # pragma: no cover - keep auto-refresh alive
        st.error(f"实时面板刷新失败（将自动重试）：{exc}")


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
