from __future__ import annotations

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.state import init_session_state
from frontend.ui.health_panel import get_cached_health, render_health_panel
from frontend.ui.input_panel import handle_run_action, render_input_panel, render_sidebar_settings
from frontend.ui.live_workspace import live_workspace_fragment, render_idle_status, render_paused_panel
from frontend.ui.output_panel import render_output_panel
from frontend.ui.theme import inject_global_styles


st.set_page_config(page_title="Specs-First", layout="wide", page_icon="🔎")
inject_global_styles()

st.title("Specs-First · 不服跑个分")
st.caption("官方冰冷参数 · 民间翻车黑料 · 真实到手价 · 证据链可追溯")

init_session_state()
get_cached_health()

with st.sidebar:
    settings = render_sidebar_settings()

input_ctx = render_input_panel(settings)
handle_run_action(input_ctx)

st.markdown("---")
render_health_panel(compact=bool(st.session_state.get("active_task_id")))

if st.session_state.get("active_task_id"):
    live_workspace_fragment()
elif st.session_state.get("paused_task_id"):
    render_paused_panel()
else:
    render_idle_status()

st.markdown("---")
if not st.session_state.get("active_task_id"):
    render_output_panel()
