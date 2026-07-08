from __future__ import annotations

import re


# Generic evaluation slots used by keyword fallback extractors (non-mock code paths).
GENERIC_PARAMETER_SLOTS = tuple(f"parameter_{chr(ord('a') + index)}" for index in range(8))


def slugify_spec_name(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", label.strip().lower())
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", cleaned, flags=re.UNICODE)
    cleaned = cleaned.strip("_")
    return cleaned or "parameter"


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
