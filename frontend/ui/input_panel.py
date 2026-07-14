from __future__ import annotations

from dataclasses import dataclass

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from frontend.api_client import get_api_client
from frontend.event_listener import start_listener
from frontend.state import reset_task_state


@dataclass
class RunSettings:
    mode: str
    use_browser: bool
    vault_path: str
    source_urls_text: str


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
    api = get_api_client()
    task_id = api.start_task(
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
    start_listener(task_id)


def render_sidebar_settings() -> RunSettings:
    st.header("运行配置")
    mode = st.selectbox("Collector mode", ["mock", "real"], help="mock 使用内置演示 SKU；real 会联网抓取")
    use_browser = st.checkbox(
        "启用 Playwright 浏览器采集",
        value=True,
        help=(
            "建议开启：淘宝弱页、YouTube PoToken、参数区截图兜底会用到。"
            "不是每个 URL 的第一选择——京东价优先 mgets，淘宝有 Cookie 优先 mtop。"
            "遇验证码会挂起任务；京东频控页不会弹 headed。"
        ),
    )
    vault_path = st.text_input("Obsidian vault path", "vault_output")
    source_urls_text = st.text_area(
        "Source URLs（强烈建议 · 跑通保底）",
        "",
        placeholder="每行一个：京东商品 + 淘宝商品 + 评测 BV/YouTube",
        help=(
            "发现层依赖搜索时易空。跑通请至少贴：1 个 item.jd.com、1 个 detail.tmall/"
            "item.taobao、1–2 个真实评测视频。验收：矩阵有槽 + 有价或规格 + 有证据即可，"
            "不要求全平台全满。也可用 .env 的 OPTIONAL_SOURCE_URLS。"
        ),
    )
    with st.expander("Real 跑通清单", expanded=False):
        st.markdown(
            """
1. `.env`：Gemini + OpenAI；`JD_COOKIE`；淘宝 Cookie/`_m_h5_tk`；B 站三 Cookie  
2. `python scripts/smoke_platforms.py --probe-gemini`  
3. 上方贴商品/评测直链 → `real` + Playwright → 开始对比  
4. 通过标准：JIT 槽位非空，且至少有价/规格/证据之一  
            """.strip()
        )
    st.markdown("---")
    st.markdown("**双脑模式**")
    st.markdown("- **Gemini**：Phase 1/2/3 文本吞噬 + OCR")
    st.markdown("- **OpenAI**：Phase 4 Structured Output 锁格式")
    st.caption("未配置 API Key 时自动降级为关键词规则引擎。")

    st.markdown("---")
    st.markdown("**本地 ASR 转写（无字幕视频）**")
    try:
        from collectors.asr import available_backend as _asr_backend

        _backend = _asr_backend()
    except Exception:
        _backend = None
    if _backend:
        st.caption(f"后端: {_backend}")
        asr_url = st.text_input(
            "视频 URL（YouTube / B 站）",
            key="asr_url",
            placeholder="https://www.youtube.com/watch?v=...",
        )
        asr_lang = st.selectbox("语言", ["auto", "zh", "en"], key="asr_lang")
        if st.button("本地转写", use_container_width=True, key="asr_run"):
            if asr_url:
                with st.spinner(f"正在转写（{_backend}）…可能需要数分钟"):
                    from pathlib import Path as _Path

                    from collectors.asr import transcribe_url as _transcribe

                    _result = _transcribe(asr_url, language=asr_lang, output_dir=_Path("vault_output/asr_cache"))
                    if _result.ok:
                        st.success(f"转写完成（{len(_result.text)} 字符，后端: {_result.backend}）")
                        st.text_area("转写结果", _result.text, height=200)
                    else:
                        st.error(f"转写失败: {_result.error}")
    else:
        st.caption("未安装 ASR 后端，请安装 `funasr`（SenseVoice）或 `faster-whisper`")

    paused_task_id = st.session_state.get("paused_task_id")
    if paused_task_id:
        st.markdown("---")
        st.warning(f"任务 `{paused_task_id}` 等待验证续传")
        if st.button("续传任务", use_container_width=True):
            resume_background_task(paused_task_id)
            st.rerun()

    return RunSettings(
        mode=mode,
        use_browser=use_browser,
        vault_path=vault_path,
        source_urls_text=source_urls_text,
    )


def render_input_panel(settings: RunSettings) -> InputContext:
    st.subheader("输入 · 对比意图")
    query = st.text_input("想对比什么？", "无线机械键盘 75%")
    category = st.text_input(
        "品类提示（可留空/填 Product；正式品类由大模型 JIT 建表）",
        "Product",
        help="不再使用预设品类模板。有图时 Gemini 识图梳理 → ChatGPT 结构化输出 5–8 个对比硬指标；无 API 时回退通用 parameter_a…h。",
    )
    try:
        from schemas.category_profile import infer_category

        _hint = infer_category(query, category)
        st.caption(f"建表前提示标签：**{_hint}** · 开始对比后会生成动态品类 Schema（槽位 + 对比关键词）")
    except Exception:
        pass

    col_discover, col_run = st.columns(2)
    with col_discover:
        discover_clicked = st.button("Phase 0 · 发现候选 SKU", use_container_width=True)
    with col_run:
        run_clicked = st.button("开始对比", type="primary", use_container_width=True)

    selected_skus: list[str] = []
    if st.session_state["candidates"]:
        st.markdown("**勾选要对比的 SKU**")
        options = [candidate["sku"] for candidate in st.session_state["candidates"]]
        default = options[:3]
        selected_skus = st.multiselect("Selected SKUs", options, default=default, label_visibility="collapsed")
    else:
        st.info(
            "可直接点击「开始对比」：mock 跑演示；real 按查询/勾选 SKU **自动搜索**证据与价格"
            "（侧边栏 Source URLs 可选，用于定点补充）。也可先「发现候选 SKU」再勾选。"
        )

    if discover_clicked:
        source_urls = [line.strip() for line in settings.source_urls_text.splitlines() if line.strip()]
        st.session_state["candidates"] = get_api_client().discover(
            query=query,
            category=category,
            mode=settings.mode,
            source_urls=source_urls,
        )[:10]
        st.session_state.pop("result", None)

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
