from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.ui.labels import build_column_labels
from frontend.ui.matrix import render_evidence_cards, render_matrix_header, render_matrix_table


def render_output_panel() -> None:
    st.subheader("输出 · 对比结果")

    result = st.session_state.get("result")
    matrix_rows = st.session_state.get("matrix_rows", [])
    profile = st.session_state.get("category_profile")
    label_map = build_column_labels(profile)

    if result and result.get("matrix", {}).get("rows"):
        rows = result.get("matrix", {}).get("rows", [])
        st.markdown("**最终对比矩阵**")
        render_matrix_header(len(rows), len(rows), live=False)
        render_matrix_table(rows, profile=profile, labels=label_map)
        render_evidence_cards(rows, expanded_only=False)
        _render_export(result)
        return

    if matrix_rows:
        total = max(int(st.session_state.get("progress_info", {}).get("total_skus") or len(matrix_rows)), len(matrix_rows))
        st.markdown("**渐进式对比矩阵**")
        render_matrix_header(len(matrix_rows), total, live=bool(st.session_state.get("active_task_id")))
        render_matrix_table(matrix_rows, profile=profile, labels=label_map)
        render_evidence_cards(matrix_rows, expanded_only=True)
        return

    st.info("完成对比后，矩阵、证据链与 CSV 导出将显示在此区域。")


def _render_export(result: dict[str, Any]) -> None:
    st.markdown("---")
    st.markdown("**导出与 Obsidian**")
    csv_export_path: Path | None = None
    for path in result.get("output_paths", []):
        st.code(str(path))
        if str(path).endswith(".csv"):
            csv_export_path = Path(path)
    if csv_export_path and csv_export_path.is_file():
        st.download_button(
            "⬇️ 下载 CSV 对比矩阵",
            data=csv_export_path.read_bytes(),
            file_name=csv_export_path.name,
            mime="text/csv",
            type="primary",
            use_container_width=True,
        )
