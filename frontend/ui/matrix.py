from __future__ import annotations

import html
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from schemas import CellStatus


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
