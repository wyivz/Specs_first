from __future__ import annotations

from typing import Any


def init_session_state() -> None:
    import streamlit as st

    defaults: dict[str, Any] = {
        "category_profile": None,
        "candidates": [],
        "active_task_id": None,
        "seen_event_count": 0,
        "matrix_rows": [],
        "events_log": [],
        "diagnostics": [],
        "total_steps": 1,
        "onboarding_dismissed": False,
        "advanced_mode": False,
        "task_completed": False,
        "progress_info": {
            "sku": "",
            "sku_index": 0,
            "total_skus": 1,
            "phase": 0,
            "phase_label": "准备中",
            "category": "",
            "progress": 0.0,
        },
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_task_state(category: str, selected_count: int) -> None:
    import streamlit as st

    st.session_state["seen_event_count"] = 0
    st.session_state["matrix_rows"] = []
    st.session_state["events_log"] = []
    st.session_state["category_profile"] = None
    st.session_state["diagnostics"] = []
    st.session_state["total_steps"] = max(selected_count, 1)
    st.session_state["progress_info"] = {
        "sku": "",
        "sku_index": 0,
        "total_skus": st.session_state["total_steps"],
        "phase": 0,
        "phase_label": "发现候选",
        "category": category,
        "progress": 0.0,
    }
    st.session_state.pop("result", None)
    st.session_state.pop("paused_task_id", None)
    st.session_state.pop("task_error", None)


def apply_event(event: dict[str, Any]) -> None:
    import streamlit as st

    payload = event.get("payload") or {}
    event_type = event.get("event_type", "")
    st.session_state["events_log"].append(f"- `{event_type}`: {event['message']}")

    progress_info = st.session_state.setdefault(
        "progress_info",
        {
            "sku": "",
            "sku_index": 0,
            "total_skus": st.session_state.get("total_steps", 1),
            "phase": 0,
            "phase_label": "准备中",
            "category": "",
            "progress": 0.0,
        },
    )

    if event_type in {
        "phase_started",
        "candidate_found",
        "category_profile_ready",
        "specs_collected",
        "findings_extracted",
        "collector_status",
        "matrix_row_updated",
    }:
        for key in ("sku", "sku_index", "total_skus", "phase", "phase_label", "category", "progress"):
            if key in payload and payload[key] is not None:
                progress_info[key] = payload[key]
        if event_type == "candidate_found" and "total_skus" in payload:
            st.session_state["total_steps"] = max(int(payload.get("total_skus") or 1), 1)

    if event_type == "category_profile_ready":
        st.session_state["category_profile"] = {
            "category": payload.get("category"),
            "slots": payload.get("slots") or [],
            "comparison_keywords": payload.get("comparison_keywords") or [],
            "search_modifiers": payload.get("search_modifiers") or [],
            "source": payload.get("source"),
        }

    if event_type == "matrix_row_updated":
        st.session_state["matrix_rows"] = payload.get("matrix_rows", st.session_state["matrix_rows"])

    if event_type in {"diagnostics_updated", "sku_failed"}:
        records = payload.get("records")
        if records:
            st.session_state["diagnostics"] = records

    if event_type == "auth_required":
        st.session_state["paused_task_id"] = event.get("task_id") or st.session_state.get("active_task_id")

    if event_type == "task_done":
        st.session_state["diagnostics"] = payload.get("diagnostics", st.session_state.get("diagnostics", []))
        if payload.get("category_profile"):
            st.session_state["category_profile"] = payload.get("category_profile")
        progress_info["progress"] = 1.0
        progress_info["phase_label"] = "完成"

    if event_type == "task_failed":
        st.session_state["task_error"] = payload.get("error") or event.get("message", "")


def compute_progress_value(status_state: str, progress_info: dict[str, Any], total_steps: int) -> float:
    total_skus = max(int(progress_info.get("total_skus") or total_steps), 1)
    phase = int(progress_info.get("phase") or 0)
    sku_index = int(progress_info.get("sku_index") or 0)

    if status_state == "RUNNING":
        computed = progress_info.get("progress")
        if computed is None:
            computed = (sku_index * 4 + max(phase - 1, 0)) / max(total_skus * 4, 1)
        return min(float(computed), 0.97)
    if status_state == "DONE":
        return 1.0
    return min(float(progress_info.get("progress") or 0), 0.95)
