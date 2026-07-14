from __future__ import annotations

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc

from collectors.embedded_browser import get_bridge


def render_embedded_browser_panel(task_id: str) -> None:
    """Live captcha-solving panel driven by ``collectors.embedded_browser``."""
    bridge = get_bridge(task_id)
    if not bridge:
        return

    st.markdown("---")
    st.subheader("🖥️ 嵌入式浏览器 — 验证码辅助")
    if bridge.url and any(host in bridge.url.lower() for host in ("taobao.com", "tmall.com")):
        st.error(
            "淘宝/天猫 **滑块验证** 请在任务栏弹出的 **Chrome/Edge 窗口** 里用鼠标拖动完成；"
            "下方截图仅供预览，点坐标无法可靠拖动滑块（易出现 error:CQAE0a）。"
            "若多次失败：用日常浏览器打开同一商品页完成验证 → 更新 `.env` 的 `TAOBAO_COOKIE` → 侧边栏续传。"
        )
    else:
        st.caption("请在任务栏找到弹出的浏览器窗口完成验证；下方为实时截图预览。")
    if bridge.url:
        st.caption(f"目标页面：{bridge.url}")
    frame = bridge.latest_screenshot()
    if frame:
        st.image(frame, use_container_width=True, caption=f"实时截图 (frame #{bridge.screenshot_seq})")
    else:
        st.info("正在打开浏览器，等待首帧截图…")

    col_x, col_y, col_click = st.columns([1, 1, 1])
    with col_x:
        click_x = st.number_input("点击 X 坐标", min_value=0, value=100, step=10, key=f"eb_x_{task_id}")
    with col_y:
        click_y = st.number_input("点击 Y 坐标", min_value=0, value=100, step=10, key=f"eb_y_{task_id}")
    with col_click:
        st.write("")
        st.write("")
        if st.button("👆 点击", use_container_width=True, key=f"eb_click_{task_id}"):
            bridge.submit_command("click", x=click_x, y=click_y)

    type_text = st.text_input("输入文字（例如验证码）", key=f"eb_text_{task_id}")
    col_type, col_enter, col_scroll = st.columns(3)
    with col_type:
        if st.button("⌨️ 输入文字", use_container_width=True, key=f"eb_type_{task_id}"):
            if type_text:
                bridge.submit_command("type", text=type_text)
    with col_enter:
        if st.button("↵ 回车", use_container_width=True, key=f"eb_enter_{task_id}"):
            bridge.submit_command("key", key="Enter")
    with col_scroll:
        if st.button("⬇️ 向下滚动", use_container_width=True, key=f"eb_scroll_{task_id}"):
            bridge.submit_command("scroll", delta=400)

    if bridge.error:
        st.error(f"嵌入式浏览器错误：{bridge.error}")
    st.caption("提示：截图约每秒刷新一次；点击/输入会在下一次刷新时生效，无需切换到其他窗口。")
