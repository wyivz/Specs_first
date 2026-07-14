from __future__ import annotations

import html
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.ui.labels import build_column_labels, column_label
from schemas import CellStatus


STATUS_BADGE = {
    CellStatus.NORMAL.value: "",
    CellStatus.MISSING.value: "⚪",
    CellStatus.WARNING.value: "🟡",
    CellStatus.CONFLICT.value: "🔴",
}


def _format_cell_html(cell: dict[str, Any], labels: dict[str, str]) -> str:
    value = cell.get("value", "")
    status = cell.get("status", CellStatus.NORMAL.value)
    badge = STATUS_BADGE.get(status, "")
    css_class = ""
    if status == CellStatus.CONFLICT.value:
        css_class = "sf-conflict"
    elif status == CellStatus.WARNING.value:
        css_class = "sf-warning"

    if value is None or value == "":
        if status == CellStatus.MISSING.value:
            display = '<span class="sf-missing">—</span>'
        else:
            display = ""
    else:
        display = html.escape(str(value))

    if badge:
        display += f'<span class="sf-badge" title="{html.escape(status)}">{badge}</span>'

    evidence_items = cell.get("evidence") or []
    if evidence_items and status in {CellStatus.WARNING.value, CellStatus.CONFLICT.value}:
        first = evidence_items[0]
        url = html.escape(str(first.get("url", "")))
        excerpt = html.escape(str(first.get("excerpt", "证据"))[:48])
        platform = html.escape(str(first.get("platform", "")))
        display += f'<a class="sf-evidence-link" href="{url}" target="_blank">📎 {platform}: {excerpt}</a>'
        if len(evidence_items) > 1:
            display += f'<span class="sf-evidence-link">+{len(evidence_items) - 1} 条</span>'

    return f'<td class="{css_class}">{display}</td>'


def render_matrix_table(
    rows: list[dict[str, Any]],
    *,
    profile: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
) -> None:
    if not rows:
        st.info("对比矩阵将在任务进行中逐行出现，请稍候…")
        return

    label_map = labels or build_column_labels(profile)

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
    headers = [column_label(key, label_map) for key in keys]

    table_html = [
        '<div class="sf-table-wrap"><table class="sf-table"><thead><tr>',
        *[f'<th class="{"sf-sticky" if i == 0 else ""}">{html.escape(header)}</th>' for i, header in enumerate(headers)],
        "</tr></thead><tbody>",
    ]

    for row in rows:
        table_html.append("<tr>")
        for idx, key in enumerate(keys):
            cell = row.get(key, {})
            if idx == 0:
                value = html.escape(str(cell.get("value", "")))
                status = cell.get("status", CellStatus.NORMAL.value)
                badge = STATUS_BADGE.get(status, "")
                extra = f'<span class="sf-badge">{badge}</span>' if badge else ""
                table_html.append(f'<td class="sf-sticky">{value}{extra}</td>')
            else:
                table_html.append(_format_cell_html(cell, label_map))
        table_html.append("</tr>")
    table_html.append("</tbody></table></div>")
    st.markdown("".join(table_html), unsafe_allow_html=True)


def render_evidence_cards(rows: list[dict[str, Any]], *, expanded_only: bool = True) -> None:
    conflict_rows = []
    for row in rows:
        has_issue = any(
            cell.get("status") in {CellStatus.WARNING.value, CellStatus.CONFLICT.value}
            for key, cell in row.items()
            if key != "sku"
        )
        if has_issue:
            conflict_rows.append(row)

    if not conflict_rows:
        return

    with st.expander(f"⚠ 冲突与证据详情（{len(conflict_rows)} 个 SKU）", expanded=not expanded_only):
        for row in conflict_rows:
            sku = row.get("sku", {}).get("value", "Unknown SKU")
            st.markdown(f"**{sku}**")
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
                st.markdown(f"- **{title}**")
                if warning:
                    st.caption(warning.get("official_claim", ""))
                    st.write(warning.get("real_world_claim", ""))
                for evidence in evidence_items:
                    st.markdown(
                        f"  - **{evidence.get('platform')} / {evidence.get('author')}**: "
                        f"[{evidence.get('excerpt')}]({evidence.get('url')})"
                    )


def render_matrix_header(total_ready: int, total_expected: int, *, live: bool = False) -> None:
    live_pill = '<span class="sf-pill sf-pill-live">实时更新中</span>' if live else ""
    st.markdown(
        f'<div class="sf-badge-row">{live_pill}'
        f'<span class="sf-pill">已就绪 {total_ready} / {total_expected} 行</span></div>',
        unsafe_allow_html=True,
    )
