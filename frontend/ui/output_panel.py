from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.ui.matrix import render_evidence_cards, render_matrix_table


def render_output_panel() -> None:
    st.subheader("输出 · 对比矩阵与导出")

    result = st.session_state.get("result")
    matrix_rows = st.session_state.get("matrix_rows", [])

    if result and result.get("matrix", {}).get("rows"):
        st.markdown("**最终结果快照**")
        render_matrix_table(result.get("matrix", {}).get("rows", []))
        render_evidence_cards(result.get("matrix", {}).get("rows", []))
        _render_export(result)
    elif matrix_rows:
        st.markdown("**渐进式对比矩阵**")
        render_matrix_table(matrix_rows)
        render_evidence_cards(matrix_rows)
    else:
        st.info("对比矩阵将在任务运行过程中逐行出现，完成后可下载 CSV 与查看 Obsidian 路径。")

    if result and not result.get("matrix", {}).get("rows") and matrix_rows:
        _render_export(result)


def _render_export(result: dict[str, Any]) -> None:
    st.markdown("**Obsidian 输出**")
    csv_export_path: Path | None = None
    for path in result.get("output_paths", []):
        st.code(str(path))
        if str(path).endswith(".csv"):
            csv_export_path = Path(path)
    if csv_export_path and csv_export_path.is_file():
        st.download_button(
            "下载 CSV 对比矩阵",
            data=csv_export_path.read_bytes(),
            file_name=csv_export_path.name,
            mime="text/csv",
            use_container_width=True,
        )
