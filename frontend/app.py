from __future__ import annotations

import html
import time
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.api_client import get_api_client
from collectors.embedded_browser import get_bridge
from schemas import CellStatus, to_dict


STATUS_BADGE = {
    CellStatus.NORMAL.value: "",
    CellStatus.MISSING.value: "⚪",
    CellStatus.WARNING.value: "🟡",
    CellStatus.CONFLICT.value: "🔴",
}


def render_matrix_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info("Comparison rows will appear here as each SKU finishes processing.")
        return

    priority = ["sku", "brand"]
    trailer = ["price_real_world_min", "evidence_confidence_avg", "critical_flaws", "arbitration_summary"]
    ordered_keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in ordered_keys:
                ordered_keys.append(key)
    keys = [key for key in priority if key in ordered_keys]
    keys.extend(key for key in ordered_keys if key not in priority and key not in trailer)
    keys.extend(key for key in trailer if key in ordered_keys)
    headers = [key.replace("_", " ").title() for key in keys]

    table_html = [
        "<style>",
        ".sf-table { width:100%; border-collapse:collapse; font-size:0.92rem; }",
        ".sf-table th, .sf-table td { border:1px solid #333; padding:8px 10px; vertical-align:top; }",
        ".sf-table th { background:#1f2937; color:#f9fafb; position:sticky; top:0; }",
        ".sf-badge { margin-left:6px; font-size:0.85rem; }",
        ".sf-conflict { background:#3f1d1d; }",
        ".sf-warning { background:#3f331d; }",
        "</style>",
        '<table class="sf-table"><thead><tr>',
        *[f"<th>{html.escape(header)}</th>" for header in headers],
        "</tr></thead><tbody>",
    ]

    for row in rows:
        table_html.append("<tr>")
        for key in keys:
            cell = row.get(key, {})
            value = cell.get("value", "")
            status = cell.get("status", CellStatus.NORMAL.value)
            badge = STATUS_BADGE.get(status, "")
            css_class = ""
            if status == CellStatus.CONFLICT.value:
                css_class = "sf-conflict"
            elif status == CellStatus.WARNING.value:
                css_class = "sf-warning"
            display = html.escape(str(value if value is not None else ""))
            if badge:
                display += f'<span class="sf-badge" title="{html.escape(status)}">{badge}</span>'
            table_html.append(f'<td class="{css_class}">{display}</td>')
        table_html.append("</tr>")
    table_html.append("</tbody></table>")
    st.markdown("".join(table_html), unsafe_allow_html=True)


def render_evidence_cards(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        sku = row.get("sku", {}).get("value", "Unknown SKU")
        st.markdown(f"### {sku}")
        for key, cell in row.items():
            if key == "sku":
                continue
            if cell.get("status") not in {CellStatus.WARNING.value, CellStatus.CONFLICT.value}:
                continue
            evidence_items = cell.get("evidence") or []
            warning = cell.get("warning")
            title = key
            if warning:
                title = f"{key} — {warning.get('arbitration_summary', '')}"
            with st.expander(f"⚠ {title}", expanded=cell.get("status") == CellStatus.CONFLICT.value):
                if warning:
                    st.caption(warning.get("official_claim", ""))
                    st.write(warning.get("real_world_claim", ""))
                for evidence in evidence_items:
                    st.markdown(
                        f"- **{evidence.get('platform')} / {evidence.get('author')}**: "
                        f"[{evidence.get('excerpt')}]({evidence.get('url')})"
                    )


def render_embedded_browser_panel(task_id: str) -> None:
    """Live captcha-solving panel driven by ``collectors.embedded_browser``.

    Instead of requiring the user to alt-tab into the separate headed
    Playwright OS window, this renders the same live page as a
    continuously-refreshed screenshot inside the Streamlit page, and relays
    clicks/typed text back into that page via the ``BrowserBridge`` mailbox.
    """
    bridge = get_bridge(task_id)
    if not bridge:
        return
    st.markdown("---")
    st.subheader("🖥️ 嵌入式浏览器 — 请在下方完成验证")
    if bridge.url:
        st.caption(f"目标页面：{bridge.url}")
    frame = bridge.latest_screenshot()
    if frame:
        st.image(frame, use_container_width=True, caption=f"实时截图 (frame #{bridge.screenshot_seq})")
    else:
        st.info("正在打开浏览器，等待首帧截图…")

    col_x, col_y, col_click = st.columns([1, 1, 1])
    with col_x:
        click_x = st.number_input("点击 X 坐标", min_value=0, value=100, step=10, key=f"eb_x_{task_id}")
    with col_y:
        click_y = st.number_input("点击 Y 坐标", min_value=0, value=100, step=10, key=f"eb_y_{task_id}")
    with col_click:
        st.write("")
        st.write("")
        if st.button("👆 点击", use_container_width=True, key=f"eb_click_{task_id}"):
            bridge.submit_command("click", x=click_x, y=click_y)

    type_text = st.text_input("输入文字（例如验证码）", key=f"eb_text_{task_id}")
    col_type, col_enter, col_scroll = st.columns(3)
    with col_type:
        if st.button("⌨️ 输入文字", use_container_width=True, key=f"eb_type_{task_id}"):
            if type_text:
                bridge.submit_command("type", text=type_text)
    with col_enter:
        if st.button("↵ 回车", use_container_width=True, key=f"eb_enter_{task_id}"):
            bridge.submit_command("key", key="Enter")
    with col_scroll:
        if st.button("⬇️ 向下滚动", use_container_width=True, key=f"eb_scroll_{task_id}"):
            bridge.submit_command("scroll", delta=400)

    if bridge.error:
        st.error(f"嵌入式浏览器错误：{bridge.error}")
    st.caption("提示：截图约每秒刷新一次；点击/输入会在下一次刷新时生效，无需切换到其他窗口。")


def start_background_task(
    query: str,
    category: str,
    mode: str,
    source_urls: list[str],
    selected_skus: list[str] | None,
    vault_path: str,
    use_browser: bool = False,
) -> None:
    api = get_api_client()
    task_id = api.start_task(
        query=query,
        category=category,
        selected_skus=selected_skus,
        source_urls=source_urls,
        mode=mode,
        vault_path=vault_path,
        use_browser=use_browser,
    )
    st.session_state["active_task_id"] = task_id
    st.session_state["seen_event_count"] = 0
    st.session_state["matrix_rows"] = []
    st.session_state["events_log"] = []
    st.session_state["total_steps"] = max(len(selected_skus or []), 1)
    st.session_state.pop("result", None)
    st.session_state.pop("paused_task_id", None)


def resume_background_task(task_id: str) -> None:
    get_api_client().resume_auth(task_id, use_browser=True)
    st.session_state["active_task_id"] = task_id
    st.session_state["seen_event_count"] = 0
    st.session_state.setdefault("matrix_rows", [])
    st.session_state.setdefault("events_log", [])
    st.session_state.setdefault("total_steps", 1)
    st.session_state.pop("paused_task_id", None)


def render_active_task() -> bool:
    """Poll the background task via the HTTP API and render live progress."""
    task_id = st.session_state.get("active_task_id")
    if not task_id:
        return False

    api = get_api_client()
    status = api.get_task(task_id)
    events = api.events_snapshot(task_id)
    new_events = events[st.session_state.get("seen_event_count", 0):]
    st.session_state["seen_event_count"] = len(events)

    for event in new_events:
        st.session_state["events_log"].append(f"- `{event['event_type']}`: {event['message']}")
        payload = event.get("payload") or {}
        if event["event_type"] == "matrix_row_updated":
            st.session_state["matrix_rows"] = payload.get("matrix_rows", st.session_state["matrix_rows"])
        if event["event_type"] == "diagnostics_updated":
            st.session_state["diagnostics"] = payload.get("records", [])
        if event["event_type"] == "task_done":
            st.session_state["diagnostics"] = payload.get("diagnostics", st.session_state.get("diagnostics", []))

    progress_text = new_events[-1]["message"] if new_events else "Running..."
    total_steps = st.session_state.get("total_steps", 1)
    completed = len(st.session_state["matrix_rows"])
    progress_value = 1.0 if status["state"] != "RUNNING" else min(completed / total_steps, 0.95)
    st.progress(progress_value, text=progress_text)

    st.markdown("#### Live event stream\n" + "\n".join(st.session_state["events_log"][-12:]))

    st.subheader("Progressive comparison matrix")
    render_matrix_table(st.session_state["matrix_rows"])
    render_evidence_cards(st.session_state["matrix_rows"])

    render_embedded_browser_panel(task_id)

    if status["state"] in {"DONE", "FAILED", "PAUSED_NEED_AUTH"}:
        st.session_state.pop("active_task_id", None)
        if status["state"] == "DONE":
            st.session_state["result"] = api.get_result(task_id)
        if status["state"] == "PAUSED_NEED_AUTH":
            st.session_state["paused_task_id"] = task_id
            st.warning(
                "检测到验证码/安全检测，且嵌入式浏览器未能在超时前完成验证。"
                "任务已挂起，可在侧边栏「续传任务」重试（会重新打开嵌入式浏览器）。"
            )
        elif status["state"] == "FAILED":
            st.error(f"任务失败：{status.get('error', '')}")
        else:
            st.success("Obsidian assets written.")
            result = st.session_state.get("result")
            if result:
                for path in result.get("output_paths", []):
                    st.code(str(path))
        return False

    return True


st.set_page_config(page_title="Specs-First", layout="wide", page_icon="🔎")
st.title("Specs-First · 不服跑个分")
st.caption("官方冰冷参数 · 民间翻车黑料 · 真实到手价 · 证据链可追溯")

with st.sidebar:
    st.header("Run settings")
    mode = st.selectbox("Collector mode", ["mock", "real"], help="mock 使用内置演示 SKU；real 会联网抓取")
    use_browser = st.checkbox("启用 Playwright 浏览器采集", value=False, help="real 模式下用于复杂页面；遇验证码会挂起任务")
    vault_path = st.text_input("Obsidian vault path", "vault_output")
    source_urls_text = st.text_area("Source URLs (optional)", "", placeholder="每行一个 URL，用于定点注入证据/价格")
    st.markdown("---")
    st.markdown("**双脑模式**")
    st.markdown("- **Gemini**：Phase 1/2/3 文本吞噬 + OCR")
    st.markdown("- **OpenAI**：Phase 4 Structured Output 锁格式")
    st.caption("未配置 API Key 时自动降级为关键词规则引擎。")
    # ASR manual-trigger (P3)
    st.markdown("---")
    st.markdown("**本地 ASR 转写（无字幕视频）**")
    try:
        from collectors.asr import available_backend as _asr_backend
        _backend = _asr_backend()
    except Exception:
        _backend = None
    if _backend:
        st.caption(f"后端: {_backend}")
        asr_url = st.text_input("视频 URL（YouTube / B 站）", key="asr_url", placeholder="https://www.youtube.com/watch?v=...")
        asr_lang = st.selectbox("语言", ["auto", "zh", "en"], key="asr_lang")
        if st.button("本地转写", use_container_width=True, key="asr_run"):
            if asr_url:
                with st.spinner(f"正在转写（{_backend}）…可能需要数分钟"):
                    from collectors.asr import transcribe_url as _transcribe
                    from pathlib import Path as _Path
                    _result = _transcribe(asr_url, language=asr_lang, output_dir=_Path("vault_output/asr_cache"))
                    if _result.ok:
                        st.success(f"转写完成（{len(_result.text)} 字符，后端: {_result.backend}）")
                        st.text_area("转写结果", _result.text, height=200)
                    else:
                        st.error(f"转写失败: {_result.error}")
    else:
        st.caption("未安装 ASR 后端，请安装 `funasr`（SenseVoice）或 `faster-whisper`")

    paused_task_id = st.session_state.get("paused_task_id")
    if paused_task_id:
        st.markdown("---")
        st.warning(f"任务 `{paused_task_id}` 等待验证续传")
        if st.button("续传任务", use_container_width=True):
            resume_background_task(paused_task_id)
            st.rerun()

query = st.text_input("想对比什么？", "无线机械键盘 75%")
category = st.text_input("品类", "Product")

if "candidates" not in st.session_state:
    st.session_state["candidates"] = []

col_discover, col_run = st.columns(2)
with col_discover:
    if st.button("Phase 0 · 发现候选 SKU", use_container_width=True):
        source_urls = [line.strip() for line in source_urls_text.splitlines() if line.strip()]
        st.session_state["candidates"] = get_api_client().discover(
            query=query,
            category=category,
            mode=mode,
            source_urls=source_urls,
        )[:10]
        st.session_state.pop("result", None)

with col_run:
    run_clicked = st.button("开始对比", type="primary", use_container_width=True)

selected_skus: list[str] = []
if st.session_state["candidates"]:
    st.subheader("勾选要对比的 SKU")
    options = [candidate["sku"] for candidate in st.session_state["candidates"]]
    default = options[:3]
    selected_skus = st.multiselect("Selected SKUs", options, default=default)
else:
    st.info("可直接点击「开始对比」运行 mock 演示；或先发现候选 SKU。")

if run_clicked:
    source_urls = [line.strip() for line in source_urls_text.splitlines() if line.strip()]
    start_background_task(
        query=query,
        category=category,
        mode=mode,
        source_urls=source_urls,
        selected_skus=selected_skus or None,
        vault_path=vault_path,
        use_browser=use_browser,
    )
    st.rerun()

still_running = render_active_task()

result = st.session_state.get("result")
diagnostics = st.session_state.get("diagnostics", [])
if diagnostics:
    with st.expander("采集诊断 / 降级日志", expanded=any(item.get("level") == "error" for item in diagnostics)):
        for item in diagnostics[-40:]:
            st.markdown(f"- `{item.get('level', 'info')}` **{item.get('source')}** ({item.get('sku', 'all')}): {item.get('message')}")

if result and not still_running:
    st.subheader("Saved result snapshot")
    render_matrix_table(result.get("matrix", {}).get("rows", []))
    st.subheader("Obsidian output")
    csv_export_path = None
    for path in result.get("output_paths", []):
        st.code(str(path))
        if str(path).endswith(".csv"):
            from pathlib import Path

            csv_export_path = Path(path)
    if csv_export_path:
        st.download_button(
            "下载 CSV 对比矩阵",
            data=csv_export_path.read_bytes(),
            file_name=csv_export_path.name,
            mime="text/csv",
            use_container_width=True,
        )

if still_running:
    time.sleep(1)
    st.rerun()
