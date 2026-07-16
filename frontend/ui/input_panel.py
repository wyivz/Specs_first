from __future__ import annotations

from dataclasses import dataclass

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.api_client import get_api_client
from frontend.event_listener import start_listener
from frontend.state import reset_task_state
from frontend.ui.health_panel import get_cached_health, real_mode_ready


@dataclass
class RunSettings:
    mode: str
    use_browser: bool
    vault_path: str
    source_urls_text: str
    advanced: bool


@dataclass
class InputContext:
    query: str
    category: str
    selected_skus: list[str]
    settings: RunSettings
    discover_clicked: bool
    run_clicked: bool


def start_background_task(
    query: str,
    category: str,
    mode: str,
    source_urls: list[str],
    selected_skus: list[str] | None,
    vault_path: str,
    use_browser: bool = False,
) -> None:
    task_id = get_api_client().start_task(
        query=query,
        category=category,
        selected_skus=selected_skus,
        source_urls=source_urls,
        mode=mode,
        vault_path=vault_path,
        use_browser=use_browser,
    )
    reset_task_state(category, len(selected_skus or []))
    st.session_state["active_task_id"] = task_id
    st.session_state["task_completed"] = False
    start_listener(task_id)


def resume_background_task(task_id: str) -> None:
    get_api_client().resume_auth(task_id, use_browser=True)
    st.session_state["active_task_id"] = task_id
    st.session_state["seen_event_count"] = 0
    st.session_state.setdefault("matrix_rows", [])
    st.session_state.setdefault("events_log", [])
    st.session_state.setdefault("total_steps", 1)
    st.session_state.pop("paused_task_id", None)
    st.session_state.pop("task_error", None)
    st.session_state["task_completed"] = False
    start_listener(task_id)


def render_onboarding_banner() -> None:
    if st.session_state.get("onboarding_dismissed"):
        return
    st.markdown(
        """
<div class="sf-hero">
  <h3>三步完成对比</h3>
  <p>① 输入想对比的商品 → ② 搜索并勾选 SKU → ③ 查看实时矩阵与证据链</p>
  <p>首次使用建议选 <strong>Mock 模式</strong>，无需 API Key 即可体验完整流程。</p>
</div>
        """.strip(),
        unsafe_allow_html=True,
    )
    if st.button("知道了，不再显示", key="dismiss_onboarding"):
        st.session_state["onboarding_dismissed"] = True
        st.rerun()


def render_sidebar_settings() -> RunSettings:
    advanced = st.toggle("高级选项", value=st.session_state.get("advanced_mode", False), key="advanced_mode")

    st.header("运行配置")
    try:
        from collectors.settings import settings as collector_settings

        default_mode = collector_settings.default_mode.strip().lower()
        if default_mode not in {"mock", "real"}:
            default_mode = "mock"
    except Exception:
        default_mode = "mock"

    mode_options = ["mock", "real"]
    mode_labels = {"mock": "Mock · 离线演示", "real": "Real · 联网采集"}
    mode = st.selectbox(
        "运行模式",
        mode_options,
        index=mode_options.index(default_mode),
        format_func=lambda x: mode_labels[x],
        help="Mock 按查询生成演示数据；Real 需要 .env 凭证与 Cookie。",
    )

    if mode == "real":
        ready, reason = real_mode_ready(get_cached_health())
        if not ready:
            st.error(reason)

    use_browser = True
    vault_path = "vault_output"
    source_urls_text = ""

    if advanced:
        use_browser = st.checkbox(
            "启用 Playwright 浏览器采集",
            value=True,
            help="淘宝弱页、YouTube、截图兜底会用到 Playwright。",
        )
        vault_path = st.text_input("Obsidian vault path", "vault_output")
        source_urls_text = st.text_area(
            "Source URLs（Real 模式建议填写）",
            "",
            placeholder="每行一个：京东 / 淘宝 / 评测视频链接",
        )
        with st.expander("Real 跑通清单", expanded=False):
            st.markdown(
                """
1. `.env`：Gemini + OpenAI；`JD_COOKIE`；淘宝 Cookie；B 站 Cookie
2. `python scripts/smoke_platforms.py --probe-gemini`
3. 填写商品/评测直链 → Real + Playwright → 开始对比
                """.strip()
            )
        st.markdown("---")
        st.markdown("**本地 ASR 转写**")
        try:
            from collectors.asr import available_backend as _asr_backend

            _backend = _asr_backend()
        except Exception:
            _backend = None
        if _backend:
            st.caption(f"后端: {_backend}")
            asr_url = st.text_input("视频 URL", key="asr_url", placeholder="https://...")
            asr_lang = st.selectbox("语言", ["auto", "zh", "en"], key="asr_lang")
            if st.button("本地转写", use_container_width=True, key="asr_run"):
                if asr_url:
                    with st.spinner("转写中…"):
                        from pathlib import Path as _Path

                        from collectors.asr import transcribe_url as _transcribe

                        _result = _transcribe(asr_url, language=asr_lang, output_dir=_Path("vault_output/asr_cache"))
                        if _result.ok:
                            st.success(f"完成（{len(_result.text)} 字符）")
                            st.text_area("转写结果", _result.text, height=160)
                        else:
                            st.error(_result.error)
        else:
            st.caption("安装 `funasr` 或 `faster-whisper` 后可本地转写")

    paused_task_id = st.session_state.get("paused_task_id")
    if paused_task_id:
        st.markdown("---")
        st.warning(f"任务 `{paused_task_id[:8]}…` 等待续传")
        if st.button("续传任务", type="primary", use_container_width=True):
            resume_background_task(paused_task_id)
            st.rerun()

    return RunSettings(
        mode=mode,
        use_browser=use_browser,
        vault_path=vault_path,
        source_urls_text=source_urls_text,
        advanced=advanced,
    )


def _candidate_widget_key(sku: str) -> str:
    digest = abs(hash(sku)) % (10**10)
    return f"cand_pick_{digest}"


def _init_candidate_selection(candidates: list[dict]) -> None:
    key = "selected_candidate_skus"
    version = tuple(c.get("sku", "") for c in candidates)
    if key not in st.session_state or st.session_state.get("_candidates_version") != version:
        defaults = [c["sku"] for c in candidates[:3] if c.get("sku")]
        st.session_state[key] = defaults
        st.session_state["_candidates_version"] = version


def _render_candidate_cards(candidates: list[dict]) -> list[str]:
    _init_candidate_selection(candidates)
    selected: set[str] = set(st.session_state.get("selected_candidate_skus", []))

    st.caption("请勾选要对比的具体型号（不是评测文章标题）")
    for candidate in candidates:
        sku = candidate["sku"]
        picked = sku in selected
        brand = candidate.get("brand") or "—"
        url = candidate.get("source_url") or ""
        confidence = float(candidate.get("confidence") or 0)
        box_cols = st.columns([1, 6])
        with box_cols[0]:
            if st.checkbox("选", value=picked, key=_candidate_widget_key(sku), label_visibility="collapsed"):
                selected.add(sku)
            else:
                selected.discard(sku)
        with box_cols[1]:
            short_sku = sku if len(sku) <= 48 else sku[:45] + "…"
            source_bit = ""
            if url and "example.invalid" not in url:
                source_bit = f' · <a href="{url}" target="_blank" rel="noreferrer">来源</a>'
            st.markdown(
                f'<div class="sf-candidate"><strong>{brand}</strong> · <code>{short_sku}</code>'
                f'<br><span class="sf-muted">置信度 {confidence:.0%}'
                f"{source_bit}</span></div>",
                unsafe_allow_html=True,
            )

    st.session_state["selected_candidate_skus"] = list(selected)
    return list(selected)


def _merge_manual_skus(manual_text: str, category: str) -> None:
    """Append user-typed model names into session candidates / selection."""
    from collectors.extractors import infer_brand

    raw_parts: list[str] = []
    for chunk in (manual_text or "").replace(",", "\n").replace("，", "\n").splitlines():
        part = chunk.strip()
        if part:
            raw_parts.append(part[:120])
    if not raw_parts:
        return

    existing = list(st.session_state.get("candidates") or [])
    seen = {str(item.get("sku", "")).casefold() for item in existing}
    selected = list(st.session_state.get("selected_candidate_skus") or [])
    added = 0
    for sku in raw_parts:
        if sku.casefold() in seen:
            continue
        seen.add(sku.casefold())
        existing.append(
            {
                "sku": sku,
                "brand": infer_brand(sku),
                "category": category or "Product",
                "source_url": "https://example.invalid/manual",
                "confidence": 0.9,
            }
        )
        if sku not in selected:
            selected.append(sku)
        added += 1
    if added:
        st.session_state["candidates"] = existing
        st.session_state["selected_candidate_skus"] = selected
        st.session_state.pop("_candidates_version", None)
        st.session_state["discover_message"] = (
            f"已加入 {added} 个手动型号；请勾选后点击开始对比。"
        )
        st.session_state.pop("discover_error", None)


def render_input_panel(settings: RunSettings) -> InputContext:
    render_onboarding_banner()
    st.subheader("输入 · 对比意图")

    query = st.text_input("想对比什么？", "罗技 G304 无线游戏鼠标")
    category = st.text_input(
        "品类提示（可选，JIT 建表时会自动识别）",
        "Product",
        help="有图时 Gemini 识图 → ChatGPT 锁定 5–8 个对比硬指标。",
    )

    if query.strip() != st.session_state.get("discover_query", "") and st.session_state.get("candidates"):
        st.session_state["candidates"] = []
        st.session_state.pop("discover_message", None)
        st.session_state.pop("discover_error", None)

    try:
        from schemas.category_profile import infer_category

        _hint = infer_category(query, category)
        st.caption(f"建表前提示：**{_hint}**")
    except Exception:
        pass

    col_discover, col_run = st.columns(2)
    with col_discover:
        discover_clicked = st.button("🔍 搜索候选 SKU", use_container_width=True)
        if settings.mode == "real":
            st.caption("Real 模式需联网搜索，点击后请等待进度提示")
    with col_run:
        run_label = "▶ 开始对比（Mock 演示）" if settings.mode == "mock" else "▶ 开始对比（Real）"
        run_clicked = st.button(run_label, type="primary", use_container_width=True)

    if discover_clicked:
        source_urls = [line.strip() for line in settings.source_urls_text.splitlines() if line.strip()]
        spinner_label = (
            "正在生成 Mock 候选…"
            if settings.mode == "mock"
            else "正在搜索并抓取页面，AI 提炼可购型号…"
        )
        discovered: list[dict] = []
        progress = st.empty()
        with st.spinner(spinner_label):
            try:
                def _on_progress(message: str) -> None:
                    progress.caption(message)

                discovered = get_api_client().discover(
                    query=query,
                    category=category,
                    mode=settings.mode,
                    source_urls=source_urls,
                    quick=settings.mode == "real",
                    on_progress=_on_progress if settings.mode == "real" else None,
                )[:10]
            except Exception as exc:
                st.session_state["discover_error"] = str(exc)
                st.session_state["candidates"] = []
                st.session_state["discover_message"] = ""
            else:
                st.session_state.pop("discover_error", None)
                st.session_state["candidates"] = discovered
                st.session_state["discover_query"] = query.strip()
                st.session_state["discover_mode"] = settings.mode
                st.session_state.pop("result", None)
                st.session_state.pop("_candidates_version", None)
                if discovered:
                    st.session_state["discover_message"] = (
                        f"AI 已从页面内容提炼出 {len(discovered)} 个可购型号，"
                        "请勾选后开始对比（不是文章标题）。"
                    )
                else:
                    st.session_state["discover_message"] = (
                        "未提炼出可购型号。请确认已配置 Gemini/OpenAI Key；"
                        "也可换更具体关键词，或在下方手动添加型号。"
                    )
        progress.empty()

    if st.session_state.get("discover_error"):
        st.error(f"搜索候选失败：{st.session_state['discover_error']}")
    elif st.session_state.get("discover_message"):
        if st.session_state.get("candidates"):
            st.success(st.session_state["discover_message"])
        else:
            st.warning(st.session_state["discover_message"])

    selected_skus: list[str] = []
    if st.session_state.get("candidates"):
        discover_mode = st.session_state.get("discover_mode", settings.mode)
        discover_query = st.session_state.get("discover_query", query)
        st.markdown(f"**候选型号** · {discover_mode} · 「{discover_query}」")
        selected_skus = _render_candidate_cards(st.session_state["candidates"])
        with st.expander("手动添加型号", expanded=False):
            manual = st.text_area(
                "每行一个型号（也可用逗号分隔）",
                key="manual_sku_text",
                placeholder="罗技 G304\n雷蛇 Viper V3 Pro\n雷柏 VT9 Pro",
                height=90,
            )
            if st.button("加入候选列表", key="manual_sku_add", use_container_width=True):
                _merge_manual_skus(manual, category)
                st.rerun()
    else:
        st.info("可直接「开始对比」，或先「搜索候选 SKU」再勾选具体型号。")
        with st.expander("手动添加型号", expanded=False):
            manual = st.text_area(
                "每行一个型号（也可用逗号分隔）",
                key="manual_sku_text_empty",
                placeholder="罗技 G304\n雷蛇 Viper V3 Pro",
                height=90,
            )
            if st.button("加入候选列表", key="manual_sku_add_empty", use_container_width=True):
                _merge_manual_skus(manual, category)
                if st.session_state.get("candidates"):
                    st.session_state["discover_query"] = query.strip()
                    st.session_state["discover_mode"] = settings.mode
                st.rerun()

    return InputContext(
        query=query,
        category=category,
        selected_skus=selected_skus,
        settings=settings,
        discover_clicked=discover_clicked,
        run_clicked=run_clicked,
    )


def handle_run_action(ctx: InputContext) -> None:
    if not ctx.run_clicked:
        return

    if ctx.settings.mode == "real":
        ready, reason = real_mode_ready(get_cached_health())
        if not ready:
            st.error(reason)
            return

    source_urls = [line.strip() for line in ctx.settings.source_urls_text.splitlines() if line.strip()]
    start_background_task(
        query=ctx.query,
        category=ctx.category,
        mode=ctx.settings.mode,
        source_urls=source_urls,
        selected_skus=ctx.selected_skus or None,
        vault_path=ctx.settings.vault_path,
        use_browser=ctx.settings.use_browser,
    )
    st.rerun()
