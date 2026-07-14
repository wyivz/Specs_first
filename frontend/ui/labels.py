from __future__ import annotations

from typing import Any

# Fixed trailer / base column labels (Chinese).
STATIC_COLUMN_LABELS: dict[str, str] = {
    "sku": "SKU",
    "brand": "品牌",
    "price_real_world_min": "真实到手价",
    "critical_flaws": "关键缺陷",
    "arbitration_summary": "冲突仲裁",
    "evidence_confidence_avg": "证据置信度",
}

# Common spec slot fallbacks when JIT aliases are unavailable.
SLOT_LABEL_FALLBACKS: dict[str, str] = {
    "focal_length": "焦距",
    "max_aperture": "最大光圈",
    "mount": "卡口",
    "weight": "重量",
    "min_focus_distance": "最近对焦",
    "filter_diameter": "滤镜口径",
    "image_stabilization": "防抖",
    "sensor_size": "传感器尺寸",
    "battery_life": "续航",
    "switch_type": "轴体",
    "connectivity": "连接方式",
    "dpi": "DPI",
    "polling_rate": "回报率",
}


def build_column_labels(profile: dict[str, Any] | None) -> dict[str, str]:
    labels = dict(STATIC_COLUMN_LABELS)
    if not profile:
        labels.update(SLOT_LABEL_FALLBACKS)
        return labels

    aliases = profile.get("aliases") or {}
    for alias, slot in aliases.items():
        if slot and alias:
            labels[str(slot)] = str(alias)

    for slot in profile.get("slots") or []:
        key = str(slot)
        if key not in labels:
            labels[key] = SLOT_LABEL_FALLBACKS.get(key, key.replace("_", " "))

    return labels


def column_label(key: str, labels: dict[str, str] | None = None) -> str:
    labels = labels or STATIC_COLUMN_LABELS
    return labels.get(key, SLOT_LABEL_FALLBACKS.get(key, key.replace("_", " ")))
