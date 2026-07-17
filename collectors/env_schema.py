from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_EXAMPLE_PATH = _PROJECT_ROOT / ".env.example"

SECRET_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "JD_COOKIE",
        "TAOBAO_COOKIE",
        "TAOBAO_M_H5_TK",
        "BILIBILI_SESSDATA",
        "BILIBILI_BILI_JCT",
        "BILIBILI_DEDEUSERID",
        "BILIBILI_BUVID3",
        "YOUTUBE_COOKIE",
        "REDDIT_COOKIE",
    }
)

BOOL_KEYS = frozenset(
    {
        "GEMINI_CONTEXT_CACHE_ENABLED",
        "COLLECTION_PARALLEL_PLATFORMS",
        "YOUTUBE_ASR_FALLBACK",
        "YOUTUBE_BROWSER_TRANSCRIPT",
        "BILIBILI_ASR_FALLBACK",
        "COLLECTION_TRACE",
    }
)

INT_KEYS = frozenset(
    {
        "GEMINI_CONTEXT_CACHE_MIN_CHARS",
        "GEMINI_CONTEXT_CACHE_TTL_SECONDS",
        "ECOMMERCE_MAX_URLS_PER_PLATFORM",
        "YOUTUBE_COMMENT_MAX_PER_VIDEO",
        "BILIBILI_MAX_VIDEOS_PER_SKU",
        "BILIBILI_MAX_COMMENTS_PER_VIDEO",
    }
)

FLOAT_KEYS = frozenset(
    {
        "GEMINI_CALL_TIMEOUT_SECONDS",
        "COLLECTION_MIN_INTERVAL_SECONDS",
        "ECOMMERCE_MIN_INTERVAL_SECONDS",
        "ECOMMERCE_COLLECT_TIMEOUT_SECONDS",
        "BILIBILI_COMMENT_PAGE_DELAY_SECONDS",
        "YOUTUBE_COMMENT_DELAY_MIN",
        "YOUTUBE_COMMENT_DELAY_MAX",
    }
)

SELECT_OPTIONS: dict[str, tuple[str, ...]] = {
    "SPECS_FIRST_MODE": ("mock", "real"),
    "GEMINI_THINKING_LEVEL": ("", "minimal", "low", "medium", "high"),
}

GROUP_LABELS: dict[str, str] = {
    "ai": "AI 与运行模式",
    "storage": "存储与 Redis",
    "gemini": "Gemini 调优",
    "collection": "采集层",
    "cookies": "平台 Cookie",
    "smoke": "Smoke 与调试",
}

KEY_LABELS: dict[str, str] = {
    "OPENAI_API_KEY": "OpenAI API Key",
    "GEMINI_API_KEY": "Gemini API Key",
    "REDIS_URL": "Redis URL",
    "OBSIDIAN_VAULT_PATH": "Obsidian Vault 路径",
    "DEFAULT_OPENAI_MODEL": "OpenAI 模型",
    "DEFAULT_GEMINI_MODEL": "Gemini 模型",
    "GEMINI_THINKING_LEVEL": "Gemini Thinking 级别",
    "GEMINI_CALL_TIMEOUT_SECONDS": "Gemini 调用超时（秒）",
    "SPECS_FIRST_MODE": "默认运行模式",
    "GEMINI_CONTEXT_CACHE_ENABLED": "Gemini 上下文缓存",
    "GEMINI_CONTEXT_CACHE_MIN_CHARS": "缓存最小字符数",
    "GEMINI_CONTEXT_CACHE_TTL_SECONDS": "缓存 TTL（秒）",
    "COLLECTION_MIN_INTERVAL_SECONDS": "全局采集间隔（秒）",
    "ECOMMERCE_MIN_INTERVAL_SECONDS": "电商采集间隔（秒）",
    "ECOMMERCE_COLLECT_TIMEOUT_SECONDS": "电商采集超时（秒）",
    "COLLECTION_PARALLEL_PLATFORMS": "平台并行采集",
    "ECOMMERCE_MAX_URLS_PER_PLATFORM": "每平台最大 URL 数",
    "BILIBILI_COMMENT_PAGE_DELAY_SECONDS": "B 站评论翻页延迟（秒）",
    "YOUTUBE_COMMENT_DELAY_MIN": "YouTube 评论延迟下限（秒）",
    "YOUTUBE_COMMENT_DELAY_MAX": "YouTube 评论延迟上限（秒）",
    "YOUTUBE_COMMENT_MAX_PER_VIDEO": "每视频最大评论数",
    "YOUTUBE_ASR_FALLBACK": "YouTube ASR 兜底",
    "YOUTUBE_BROWSER_TRANSCRIPT": "YouTube 浏览器字幕",
    "YOUTUBE_COOKIE": "YouTube Cookie",
    "BILIBILI_MAX_VIDEOS_PER_SKU": "每 SKU B 站视频数",
    "BILIBILI_MAX_COMMENTS_PER_VIDEO": "每视频 B 站评论数",
    "BILIBILI_SESSDATA": "B 站 SESSDATA",
    "BILIBILI_BILI_JCT": "B 站 bili_jct",
    "BILIBILI_DEDEUSERID": "B 站 DedeUserID",
    "BILIBILI_BUVID3": "B 站 buvid3",
    "BILIBILI_ASR_FALLBACK": "B 站 ASR 兜底",
    "TAOBAO_COOKIE": "淘宝 Cookie",
    "TAOBAO_M_H5_TK": "淘宝 _m_h5_tk",
    "JD_COOKIE": "京东 Cookie",
    "REDDIT_COOKIE": "Reddit Cookie",
    "SMOKE_JD_URL": "Smoke 京东 URL",
    "SMOKE_TAOBAO_ITEM_ID": "Smoke 淘宝商品 ID",
    "SMOKE_BILIBILI_BVID": "Smoke B 站 BV 号",
    "SMOKE_YOUTUBE_URL": "Smoke YouTube URL",
    "OPTIONAL_SOURCE_URLS": "可选固定来源 URL",
    "COLLECTION_TRACE": "采集 Trace 日志",
    "COLLECTION_TRACE_DIR": "Trace 输出目录",
}

KEY_GROUPS: dict[str, str] = {
    "OPENAI_API_KEY": "ai",
    "GEMINI_API_KEY": "ai",
    "DEFAULT_OPENAI_MODEL": "ai",
    "DEFAULT_GEMINI_MODEL": "ai",
    "GEMINI_THINKING_LEVEL": "gemini",
    "GEMINI_CALL_TIMEOUT_SECONDS": "gemini",
    "SPECS_FIRST_MODE": "ai",
    "GEMINI_CONTEXT_CACHE_ENABLED": "gemini",
    "GEMINI_CONTEXT_CACHE_MIN_CHARS": "gemini",
    "GEMINI_CONTEXT_CACHE_TTL_SECONDS": "gemini",
    "REDIS_URL": "storage",
    "OBSIDIAN_VAULT_PATH": "storage",
    "COLLECTION_MIN_INTERVAL_SECONDS": "collection",
    "ECOMMERCE_MIN_INTERVAL_SECONDS": "collection",
    "ECOMMERCE_COLLECT_TIMEOUT_SECONDS": "collection",
    "COLLECTION_PARALLEL_PLATFORMS": "collection",
    "ECOMMERCE_MAX_URLS_PER_PLATFORM": "collection",
    "BILIBILI_COMMENT_PAGE_DELAY_SECONDS": "collection",
    "YOUTUBE_COMMENT_DELAY_MIN": "collection",
    "YOUTUBE_COMMENT_DELAY_MAX": "collection",
    "YOUTUBE_COMMENT_MAX_PER_VIDEO": "collection",
    "YOUTUBE_ASR_FALLBACK": "collection",
    "YOUTUBE_BROWSER_TRANSCRIPT": "collection",
    "YOUTUBE_COOKIE": "cookies",
    "BILIBILI_MAX_VIDEOS_PER_SKU": "collection",
    "BILIBILI_MAX_COMMENTS_PER_VIDEO": "collection",
    "BILIBILI_SESSDATA": "cookies",
    "BILIBILI_BILI_JCT": "cookies",
    "BILIBILI_DEDEUSERID": "cookies",
    "BILIBILI_BUVID3": "cookies",
    "BILIBILI_ASR_FALLBACK": "collection",
    "TAOBAO_COOKIE": "cookies",
    "TAOBAO_M_H5_TK": "cookies",
    "JD_COOKIE": "cookies",
    "REDDIT_COOKIE": "cookies",
    "SMOKE_JD_URL": "smoke",
    "SMOKE_TAOBAO_ITEM_ID": "smoke",
    "SMOKE_BILIBILI_BVID": "smoke",
    "SMOKE_YOUTUBE_URL": "smoke",
    "OPTIONAL_SOURCE_URLS": "smoke",
    "COLLECTION_TRACE": "smoke",
    "COLLECTION_TRACE_DIR": "smoke",
}

_SECTION_RE = re.compile(r"^#\s*---\s*(.+?)\s*---\s*$")


@dataclass(frozen=True)
class EnvFieldSpec:
    key: str
    group: str
    label: str
    field_type: str
    default: str = ""
    help: str = ""
    options: tuple[str, ...] = ()


def _infer_field_type(key: str) -> str:
    if key in SECRET_KEYS:
        return "secret"
    if key in SELECT_OPTIONS:
        return "select"
    if key in BOOL_KEYS:
        return "bool"
    if key in INT_KEYS:
        return "int"
    if key in FLOAT_KEYS:
        return "float"
    if key.endswith("_PATH") or key.endswith("_DIR") or key == "REDIS_URL":
        return "path"
    return "text"


def _group_from_section(section: str) -> str:
    lowered = section.casefold()
    if "collection" in lowered:
        return "collection"
    return "collection"


def parse_env_example(path: Path | None = None) -> list[EnvFieldSpec]:
    """Build field specs from .env.example key order and nearby comments."""
    env_path = path or _ENV_EXAMPLE_PATH
    if not env_path.is_file():
        return []

    pending_help: list[str] = []
    current_section = "ai"
    specs: list[EnvFieldSpec] = []
    seen: set[str] = set()

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            pending_help = []
            continue
        if line.startswith("#"):
            section_match = _SECTION_RE.match(line)
            if section_match:
                current_section = _group_from_section(section_match.group(1))
            else:
                pending_help.append(line.lstrip("#").strip())
            continue
        if "=" not in line:
            continue
        key, _, default = line.partition("=")
        key = key.strip()
        default = default.strip().strip('"').strip("'")
        if not key or key in seen:
            pending_help = []
            continue
        seen.add(key)

        group = KEY_GROUPS.get(key, current_section)
        help_text = " ".join(pending_help).strip()
        pending_help = []

        specs.append(
            EnvFieldSpec(
                key=key,
                group=group,
                label=KEY_LABELS.get(key, key),
                field_type=_infer_field_type(key),
                default=default,
                help=help_text,
                options=SELECT_OPTIONS.get(key, ()),
            )
        )

    return specs


def grouped_field_specs(specs: list[EnvFieldSpec] | None = None) -> list[tuple[str, str, list[EnvFieldSpec]]]:
    """Return (group_id, group_label, fields) in first-seen group order."""
    items = specs or parse_env_example()
    order: list[str] = []
    buckets: dict[str, list[EnvFieldSpec]] = {}
    for spec in items:
        if spec.group not in buckets:
            order.append(spec.group)
            buckets[spec.group] = []
        buckets[spec.group].append(spec)
    return [(group, GROUP_LABELS.get(group, group), buckets[group]) for group in order]


def all_schema_keys(specs: list[EnvFieldSpec] | None = None) -> list[str]:
    return [spec.key for spec in (specs or parse_env_example())]


def env_example_path() -> Path:
    return _ENV_EXAMPLE_PATH
