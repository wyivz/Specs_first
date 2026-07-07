from __future__ import annotations

import html
import time
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from backend.pipeline import create_pipeline
from schemas import CellStatus, TaskEvent, to_dict


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

    headers = [
        "SKU",
        "Focal Length",
        "Max Aperture",
        "Weight",
        "Optical Structure",
        "Min Focus",
        "Filter",
        "Real Price",
        "Critical Flaws",
        "Arbitration",
    ]
    keys = [
        "sku",
        "focal_length",
        "max_aperture",
        "weight",
        "optical_structure",
        "minimum_focus_distance",
        "filter_thread",
        "price_real_world_min",
        "critical_flaws",
        "arbitration_summary",
    ]

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


def run_streaming_pipeline(
    query: str,
    category: str,
    mode: str,
    source_urls: list[str],
    selected_skus: list[str] | None,
    vault_path: str,
) -> None:
    pipeline = create_pipeline(mode=mode, source_urls=source_urls, vault_path=vault_path)
    progress = st.progress(0, text="Starting Specs-First pipeline...")
    log_box = st.empty()
    matrix_box = st.empty()
    evidence_box = st.empty()
    output_box = st.empty()

    events: list[TaskEvent] = []
    matrix_rows: list[dict[str, Any]] = []
    total_steps = max(len(selected_skus or ["a", "b", "c"]), 1)
    completed_rows = 0

    def on_event(event: TaskEvent) -> None:
        nonlocal completed_rows, matrix_rows
        events.append(event)
        log_lines = [f"- `{event.event_type}`: {event.message}" for event in events[-12:]]
        log_box.markdown("#### Live event stream\n" + "\n".join(log_lines))

        payload = event.payload or {}
        if event.event_type == "matrix_row_updated":
            matrix_rows = payload.get("matrix_rows", matrix_rows)
            completed_rows += 1
            progress.progress(min(completed_rows / total_steps, 0.95), text=event.message)
            with matrix_box.container():
                st.subheader("Progressive comparison matrix")
                render_matrix_table(matrix_rows)
                render_evidence_cards(matrix_rows)
        if event.event_type == "task_done":
            progress.progress(1.0, text="Done")
            output_box.success("Obsidian assets written.")
            for path in payload.get("output_paths", []):
                output_box.code(path)

    result = pipeline.run(
        query=query,
        category=category,
        selected_skus=selected_skus,
        source_urls=source_urls,
        on_event=on_event,
    )
    st.session_state["result"] = result
    with matrix_box.container():
        st.subheader("Final comparison matrix")
        render_matrix_table([to_dict(row) for row in result.matrix.rows])
    with evidence_box.container():
        st.subheader("Evidence cards")
        render_evidence_cards([to_dict(row) for row in result.matrix.rows])


st.set_page_config(page_title="Specs-First", layout="wide", page_icon="🔎")
st.title("Specs-First · 不服跑个分")
st.caption("官方冰冷参数 · 民间翻车黑料 · 真实到手价 · 证据链可追溯")

with st.sidebar:
    st.header("Run settings")
    mode = st.selectbox("Collector mode", ["mock", "real"], help="mock 使用内置 Zeiss 50mm 演示数据；real 会联网抓取")
    vault_path = st.text_input("Obsidian vault path", "vault_output")
    source_urls_text = st.text_area("Source URLs (optional)", "", placeholder="每行一个 URL，用于定点注入证据/价格")
    st.markdown("---")
    st.markdown("**双脑模式**")
    st.markdown("- Gemini：Phase 2 民间脱水")
    st.markdown("- OpenAI：Phase 4 冲突仲裁")
    st.caption("未配置 API Key 时自动降级为关键词规则引擎。")

query = st.text_input("想对比什么？", "Zeiss 50mm 镜头")
category = st.text_input("品类", "Lens")

if "candidates" not in st.session_state:
    st.session_state["candidates"] = []

col_discover, col_run = st.columns(2)
with col_discover:
    if st.button("Phase 0 · 发现候选 SKU", use_container_width=True):
        pipeline = create_pipeline(mode=mode, source_urls=[line.strip() for line in source_urls_text.splitlines() if line.strip()])
        st.session_state["candidates"] = pipeline.collector.discover_candidates(query, category)[:10]
        st.session_state.pop("result", None)

with col_run:
    run_clicked = st.button("开始对比", type="primary", use_container_width=True)

selected_skus: list[str] = []
if st.session_state["candidates"]:
    st.subheader("勾选要对比的 SKU")
    options = [candidate.sku for candidate in st.session_state["candidates"]]
    default = options[:3]
    selected_skus = st.multiselect("Selected SKUs", options, default=default)
else:
    st.info("可直接点击「开始对比」运行 mock 默认三款 50mm 镜头；或先发现候选 SKU。")

if run_clicked:
    source_urls = [line.strip() for line in source_urls_text.splitlines() if line.strip()]
    with st.spinner("Specs-First 正在全网脱水..."):
        run_streaming_pipeline(
            query=query,
            category=category,
            mode=mode,
            source_urls=source_urls,
            selected_skus=selected_skus or None,
            vault_path=vault_path,
        )

result = st.session_state.get("result")
if result and not run_clicked:
    st.subheader("Saved result snapshot")
    render_matrix_table([to_dict(row) for row in result.matrix.rows])
    st.subheader("Obsidian output")
    for path in result.output_paths:
        st.code(str(path))
