from __future__ import annotations

import re
from dataclasses import dataclass, field


# Generic evaluation slots used by keyword fallback extractors (non-mock code paths).
GENERIC_PARAMETER_SLOTS = tuple(f"parameter_{chr(ord('a') + index)}" for index in range(8))


def slugify_spec_name(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", label.strip().lower())
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", cleaned, flags=re.UNICODE)
    cleaned = cleaned.strip("_")
    return cleaned or "parameter"


@dataclass(frozen=True)
class CategoryTemplate:
    """Category-specific spec schema: the 5-8 canonical 'hard slot' columns
    the plan calls for in Phase 0, plus label aliases (EN/ZH) so that
    differently-worded source labels ("Focal Length" / "焦距") collapse
    onto the same matrix column instead of fragmenting into duplicate,
    mostly-empty columns (the "dimension explosion" the plan warns about).
    """

    key: str
    match_keywords: tuple[str, ...]
    slots: tuple[str, ...]
    aliases: dict[str, str] = field(default_factory=dict)


# Each template's aliases map a lowercase label substring (EN or ZH) to one
# of the template's canonical `slots`. These are examples covering common
# categories; unmatched categories/labels gracefully fall back to
# `GENERIC_PARAMETER_SLOTS` / `slugify_spec_name` so nothing breaks for a
# category we haven't modeled yet.
CATEGORY_TEMPLATES: dict[str, CategoryTemplate] = {
    "lens": CategoryTemplate(
        key="lens",
        match_keywords=("lens", "镜头", "定焦", "变焦", "微单镜头", "单反镜头"),
        slots=(
            "focal_length",
            "max_aperture",
            "optical_structure",
            "filter_diameter",
            "weight",
            "mount",
            "min_focus_distance",
            "image_stabilization",
        ),
        aliases={
            "focal length": "focal_length",
            "焦距": "focal_length",
            "aperture": "max_aperture",
            "光圈": "max_aperture",
            "optical structure": "optical_structure",
            "镜片结构": "optical_structure",
            "镜片组": "optical_structure",
            "filter": "filter_diameter",
            "口径": "filter_diameter",
            "weight": "weight",
            "重量": "weight",
            "mount": "mount",
            "卡口": "mount",
            "minimum focus": "min_focus_distance",
            "最近对焦": "min_focus_distance",
            "stabiliz": "image_stabilization",
            "防抖": "image_stabilization",
        },
    ),
    "phone": CategoryTemplate(
        key="phone",
        match_keywords=("phone", "手机", "smartphone", "iphone"),
        slots=(
            "screen_size",
            "resolution",
            "chipset",
            "ram",
            "storage",
            "battery_capacity",
            "main_camera",
            "weight",
        ),
        aliases={
            "screen size": "screen_size",
            "屏幕尺寸": "screen_size",
            "resolution": "resolution",
            "分辨率": "resolution",
            "chipset": "chipset",
            "processor": "chipset",
            "处理器": "chipset",
            "soc": "chipset",
            "ram": "ram",
            "运行内存": "ram",
            "storage": "storage",
            "存储": "storage",
            "battery": "battery_capacity",
            "电池": "battery_capacity",
            "容量": "battery_capacity",
            "camera": "main_camera",
            "摄像头": "main_camera",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "laptop": CategoryTemplate(
        key="laptop",
        match_keywords=("laptop", "notebook", "笔记本", "笔电", "ultrabook"),
        slots=(
            "cpu",
            "gpu",
            "ram",
            "storage",
            "screen_size",
            "resolution",
            "battery_capacity",
            "weight",
        ),
        aliases={
            "cpu": "cpu",
            "processor": "cpu",
            "处理器": "cpu",
            "gpu": "gpu",
            "显卡": "gpu",
            "ram": "ram",
            "内存": "ram",
            "storage": "storage",
            "存储": "storage",
            "硬盘": "storage",
            "screen size": "screen_size",
            "屏幕尺寸": "screen_size",
            "resolution": "resolution",
            "分辨率": "resolution",
            "battery": "battery_capacity",
            "电池": "battery_capacity",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "headphone": CategoryTemplate(
        key="headphone",
        match_keywords=("headphone", "headset", "earbud", "耳机", "耳麦"),
        slots=(
            "driver_size",
            "frequency_response",
            "impedance",
            "battery_life",
            "noise_cancellation",
            "connectivity",
            "water_resistance",
            "weight",
        ),
        aliases={
            "driver": "driver_size",
            "单元": "driver_size",
            "frequency response": "frequency_response",
            "频响": "frequency_response",
            "impedance": "impedance",
            "阻抗": "impedance",
            "battery life": "battery_life",
            "续航": "battery_life",
            "anc": "noise_cancellation",
            "noise cancel": "noise_cancellation",
            "降噪": "noise_cancellation",
            "bluetooth": "connectivity",
            "蓝牙": "connectivity",
            "connectivity": "connectivity",
            "waterproof": "water_resistance",
            "防水": "water_resistance",
            "ip rating": "water_resistance",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "camera": CategoryTemplate(
        key="camera",
        match_keywords=("camera", "相机", "微单", "单反", "mirrorless", "dslr"),
        slots=(
            "sensor_size",
            "resolution",
            "iso_range",
            "autofocus_points",
            "video_resolution",
            "battery_life",
            "mount",
            "weight",
        ),
        aliases={
            "sensor": "sensor_size",
            "画幅": "sensor_size",
            "传感器": "sensor_size",
            "resolution": "resolution",
            "像素": "resolution",
            "分辨率": "resolution",
            "iso": "iso_range",
            "autofocus": "autofocus_points",
            "对焦点": "autofocus_points",
            "video": "video_resolution",
            "视频": "video_resolution",
            "battery life": "battery_life",
            "续航": "battery_life",
            "mount": "mount",
            "卡口": "mount",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "monitor": CategoryTemplate(
        key="monitor",
        match_keywords=("monitor", "显示器", "display panel"),
        slots=(
            "screen_size",
            "resolution",
            "refresh_rate",
            "panel_type",
            "response_time",
            "brightness",
            "color_gamut",
            "weight",
        ),
        aliases={
            "screen size": "screen_size",
            "尺寸": "screen_size",
            "resolution": "resolution",
            "分辨率": "resolution",
            "refresh rate": "refresh_rate",
            "刷新率": "refresh_rate",
            "panel": "panel_type",
            "面板": "panel_type",
            "response time": "response_time",
            "响应时间": "response_time",
            "brightness": "brightness",
            "亮度": "brightness",
            "color gamut": "color_gamut",
            "色域": "color_gamut",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "keyboard": CategoryTemplate(
        key="keyboard",
        match_keywords=("keyboard", "键盘"),
        slots=(
            "switch_type",
            "layout",
            "connectivity",
            "battery_life",
            "keycap_material",
            "backlight",
            "hot_swappable",
            "weight",
        ),
        aliases={
            "switch": "switch_type",
            "轴体": "switch_type",
            "layout": "layout",
            "布局": "layout",
            "connectivity": "connectivity",
            "连接方式": "connectivity",
            "bluetooth": "connectivity",
            "battery life": "battery_life",
            "续航": "battery_life",
            "keycap": "keycap_material",
            "键帽": "keycap_material",
            "backlight": "backlight",
            "背光": "backlight",
            "hot-swap": "hot_swappable",
            "热插拔": "hot_swappable",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "drone": CategoryTemplate(
        key="drone",
        match_keywords=("drone", "无人机", "quadcopter"),
        slots=(
            "max_flight_time",
            "max_range",
            "camera_resolution",
            "max_speed",
            "obstacle_avoidance",
            "battery_capacity",
            "gimbal_stabilization",
            "weight",
        ),
        aliases={
            "flight time": "max_flight_time",
            "续航": "max_flight_time",
            "range": "max_range",
            "图传距离": "max_range",
            "camera": "camera_resolution",
            "摄像头": "camera_resolution",
            "max speed": "max_speed",
            "最大速度": "max_speed",
            "obstacle": "obstacle_avoidance",
            "避障": "obstacle_avoidance",
            "battery": "battery_capacity",
            "电池": "battery_capacity",
            "gimbal": "gimbal_stabilization",
            "云台": "gimbal_stabilization",
            "weight": "weight",
            "重量": "weight",
        },
    ),
    "wearable": CategoryTemplate(
        key="wearable",
        match_keywords=("watch", "手表", "band", "手环", "wearable"),
        slots=(
            "battery_life",
            "display_type",
            "water_resistance",
            "sensors",
            "connectivity",
            "gps",
            "compatibility",
            "weight",
        ),
        aliases={
            "battery life": "battery_life",
            "续航": "battery_life",
            "display": "display_type",
            "屏幕": "display_type",
            "waterproof": "water_resistance",
            "防水": "water_resistance",
            "sensor": "sensors",
            "传感器": "sensors",
            "bluetooth": "connectivity",
            "连接": "connectivity",
            "gps": "gps",
            "compatib": "compatibility",
            "兼容": "compatibility",
            "weight": "weight",
            "重量": "weight",
        },
    ),
}


def resolve_category_key(category: str) -> str:
    """Map a free-form category string (from Phase 0 disambiguation) onto
    one of the known ``CATEGORY_TEMPLATES`` keys, or ``"generic"`` when
    no template matches. Unmatched categories still work fine — they just
    use the category-agnostic slot/alias-free fallback.
    """
    lowered = (category or "").strip().lower()
    if not lowered:
        return "generic"
    for template in CATEGORY_TEMPLATES.values():
        if any(keyword in lowered for keyword in template.match_keywords):
            return template.key
    return "generic"


def canonical_slots(category: str) -> tuple[str, ...]:
    """The 5-8 canonical hard-spec column names for a category, or the
    generic ``parameter_a..h`` slots when the category is unmodeled.
    """
    template = CATEGORY_TEMPLATES.get(resolve_category_key(category))
    return template.slots if template else GENERIC_PARAMETER_SLOTS


def normalize_spec_name(label: str, category: str = "") -> str:
    """Normalize an extracted spec label to a canonical column name.

    Tries the category template's aliases first (so "Focal Length" and
    "焦距" both become ``focal_length``); falls back to ``slugify_spec_name``
    for anything the template doesn't recognize, so unusual/independent
    attributes still land in a stable, unique column via
    ``spec_highlights``-style behavior rather than being dropped.
    """
    template = CATEGORY_TEMPLATES.get(resolve_category_key(category))
    if template:
        lowered = label.strip().lower()
        for alias, canonical in template.aliases.items():
            if alias in lowered:
                return canonical
    return slugify_spec_name(label)


def video_search_queries(sku: str) -> list[tuple[str, str]]:
    return [
        ("Bilibili", f"{sku} site:bilibili.com 评测 缺点 问题 翻车 体验"),
        ("YouTube", f"{sku} site:youtube.com review defect issue problem quality"),
    ]


def forum_search_queries(sku: str) -> list[tuple[str, str]]:
    return [
        ("Chiphell", f"{sku} site:chiphell.com 缺点 品控 翻车 问题 体验"),
        ("Reddit", f"{sku} site:reddit.com defect issue quality problem review"),
    ]


def ecommerce_search_queries(sku: str) -> list[tuple[str, str]]:
    return [
        ("JD", f"{sku} site:jd.com 到手价 优惠券 百亿补贴"),
        ("Taobao/Tmall", f"{sku} site:taobao.com OR site:tmall.com 到手价 券后"),
    ]


def real_world_issue_patterns() -> list[str]:
    """Category-agnostic defect / complaint hints for evidence extraction."""
    return [
        r"缺陷|故障|损坏|broken|defect|fail(?:ure|ed)?",
        r"品控|质量问题|quality control|sample variation|unit variation",
        r"卡顿|延迟|lag|slow|unresponsive|sticky",
        r"噪音|异响|noise|rattle|buzz",
        r"过热|overheat|thermal|温度",
        r"续航|battery life|standby drain",
        r"虚标|夸大|misleading|overpromise",
        r"劝退|翻车|regret|disappoint|avoid",
        r"售后|warranty|support|repair",
    ]


def review_content_patterns() -> list[str]:
    """Hints that a text snippet is a substantive user review, not boilerplate."""
    return [
        r"缺点|问题|不足|issue|problem|defect|complaint|concern",
        r"评测|review|体验|experience|hands-on|长期|after\s+\d+\s+(?:days|weeks|months)",
        r"翻车|劝退|regret|disappoint|not recommend",
    ]


def default_category() -> str:
    return "Product"
