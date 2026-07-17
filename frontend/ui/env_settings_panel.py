from __future__ import annotations

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from collectors.env_schema import SECRET_KEYS, EnvFieldSpec, grouped_field_specs, parse_env_example
from collectors.env_store import apply_updates, bootstrap_env_from_example, dotenv_path, read_env_file
from collectors.settings import reload_settings
from frontend.ui.health_panel import refresh_health_cache

_DRAFT_KEY = "env_draft"
_PANEL_SKIP_KEYS = frozenset({"SPECS_FIRST_MODE"})


def _widget_key(spec: EnvFieldSpec) -> str:
    return f"env_field_{spec.key}"


def _clear_env_widgets() -> None:
    for spec in parse_env_example():
        st.session_state.pop(_widget_key(spec), None)


def ensure_env_draft(*, force: bool = False) -> dict[str, str]:
    if force or _DRAFT_KEY not in st.session_state:
        bootstrap_env_from_example()
        st.session_state[_DRAFT_KEY] = read_env_file()
        if force:
            _clear_env_widgets()
    return dict(st.session_state[_DRAFT_KEY])


def get_env_draft() -> dict[str, str]:
    return ensure_env_draft()


def set_env_draft_value(key: str, value: str) -> None:
    draft = ensure_env_draft()
    draft[key] = value
    st.session_state[_DRAFT_KEY] = draft


def reset_env_draft() -> None:
    bootstrap_env_from_example()
    st.session_state[_DRAFT_KEY] = read_env_file()
    _clear_env_widgets()


def sync_mode_to_env_draft(mode: str) -> None:
    set_env_draft_value("SPECS_FIRST_MODE", mode)


def default_mode_from_draft() -> str:
    mode = get_env_draft().get("SPECS_FIRST_MODE", "mock").strip().lower()
    return mode if mode in {"mock", "real"} else "mock"


def _bool_value(raw: str) -> bool:
    return raw.strip().lower() not in {"0", "false", "no", ""}


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _render_field(spec: EnvFieldSpec, draft: dict[str, str], *, disabled: bool) -> None:
    key = spec.key
    widget_key = _widget_key(spec)
    current = draft.get(key, spec.default)

    if spec.field_type == "secret":
        # Seed widget state from .env so Streamlit's native eye toggle reveals real values.
        if widget_key not in st.session_state:
            st.session_state[widget_key] = current
        elif not str(st.session_state.get(widget_key, "")).strip() and current.strip():
            st.session_state[widget_key] = current
        draft[key] = st.text_input(
            spec.label,
            type="password",
            help=spec.help or None,
            key=widget_key,
            disabled=disabled,
        )
        return

    if spec.field_type == "bool":
        draft[key] = _format_bool(
            st.toggle(
                spec.label,
                value=_bool_value(current),
                help=spec.help or None,
                key=widget_key,
                disabled=disabled,
            )
        )
        return

    if spec.field_type == "select":
        options = list(spec.options) or [current]
        try:
            index = options.index(current)
        except ValueError:
            index = 0
        labels = {opt: opt or "（默认）" for opt in options}
        draft[key] = st.selectbox(
            spec.label,
            options,
            index=index,
            format_func=lambda x, labels=labels: labels.get(x, x),
            help=spec.help or None,
            key=widget_key,
            disabled=disabled,
        )
        return

    if spec.field_type == "int":
        try:
            numeric = int(float(current or spec.default or "0"))
        except ValueError:
            numeric = 0
        draft[key] = str(
            st.number_input(
                spec.label,
                value=numeric,
                step=1,
                help=spec.help or None,
                key=widget_key,
                disabled=disabled,
            )
        )
        return

    if spec.field_type == "float":
        try:
            numeric = float(current or spec.default or "0")
        except ValueError:
            numeric = 0.0
        draft[key] = str(
            st.number_input(
                spec.label,
                value=numeric,
                step=0.1,
                format="%.2f",
                help=spec.help or None,
                key=widget_key,
                disabled=disabled,
            )
        )
        return

    if spec.field_type == "path":
        draft[key] = st.text_input(
            spec.label,
            value=current,
            help=spec.help or None,
            key=widget_key,
            disabled=disabled,
        )
        return

    draft[key] = st.text_input(
        spec.label,
        value=current,
        help=spec.help or None,
        key=widget_key,
        disabled=disabled,
    )


def _audit_summary(draft: dict[str, str]) -> str:
    specs = parse_env_example()
    present = [spec.key for spec in specs if draft.get(spec.key, "").strip()]
    empty = [spec.key for spec in specs if spec.key not in present]
    secret_present = [spec.key for spec in specs if spec.field_type == "secret" and spec.key in present]
    lines = [
        f"已配置 {len(present)} / {len(specs)} 项",
        f"凭证类已设置: {len(secret_present)}",
    ]
    if empty:
        lines.append(f"未填: {', '.join(empty[:8])}{'…' if len(empty) > 8 else ''}")
    return " · ".join(lines)


def render_env_settings_panel(*, task_running: bool = False) -> None:
    ensure_env_draft()
    draft = get_env_draft()
    env_path = dotenv_path()

    with st.expander("环境配置 (.env)", expanded=False):
        st.caption(f"配置文件: `{env_path}`")
        st.caption(_audit_summary(draft))

        if task_running:
            st.warning("任务运行中，暂不可保存。可先浏览，停止任务后再写入 .env。")

        for group_id, group_label, fields in grouped_field_specs():
            visible = [field for field in fields if field.key not in _PANEL_SKIP_KEYS]
            if not visible:
                continue
            with st.expander(group_label, expanded=group_id in {"ai", "cookies"}):
                for spec in visible:
                    _render_field(spec, draft, disabled=task_running)

        st.session_state[_DRAFT_KEY] = draft

        col_reload, col_save = st.columns(2)
        with col_reload:
            if st.button("重新加载", use_container_width=True, key="env_reload"):
                reset_env_draft()
                st.rerun()
        with col_save:
            save_clicked = st.button(
                "保存到 .env",
                type="primary",
                use_container_width=True,
                key="env_save",
                disabled=task_running,
            )

        if save_clicked:
            apply_updates(
                dict(draft),
                skip_empty_secrets=True,
                secret_keys=SECRET_KEYS,
            )
            reload_settings(overwrite_all=True)
            refresh_health_cache()
            reset_env_draft()
            st.success("已保存到 .env 并重新加载配置")
            st.rerun()


def render_env_field_by_key(key: str, *, label: str | None = None, disabled: bool = False) -> str:
    """Render a single schema-backed field (used when a key is managed outside the panel)."""
    specs = {spec.key: spec for spec in parse_env_example()}
    spec = specs.get(key)
    if spec is None:
        return get_env_draft().get(key, "")
    draft = ensure_env_draft()
    if label:
        spec = EnvFieldSpec(
            key=spec.key,
            group=spec.group,
            label=label,
            field_type=spec.field_type,
            default=spec.default,
            help=spec.help,
            options=spec.options,
        )
    _render_field(spec, draft, disabled=disabled)
    st.session_state[_DRAFT_KEY] = draft
    return draft.get(key, "")
