from __future__ import annotations

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.state import init_session_state
from frontend.ui.input_panel import handle_run_action, render_input_panel, render_sidebar_settings
from frontend.ui.output_panel import render_output_panel
from frontend.ui.status_panel import live_status_fragment, render_status_panel_idle


st.set_page_config(page_title="Specs-First", layout="wide", page_icon="🔎")
st.title("Specs-First · 不服跑个分")
st.caption("官方冰冷参数 · 民间翻车黑料 · 真实到手价 · 证据链可追溯")

init_session_state()

with st.sidebar:
    settings = render_sidebar_settings()

input_ctx = render_input_panel(settings)
handle_run_action(input_ctx)

st.markdown("---")

if st.session_state.get("active_task_id"):
    live_status_fragment()
else:
    render_status_panel_idle()

st.markdown("---")
render_output_panel()
