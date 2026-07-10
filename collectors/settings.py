from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_model: str = os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4o-mini")
    gemini_model: str = os.getenv("DEFAULT_GEMINI_MODEL", "gemini-2.5-flash")

    gemini_context_cache_enabled: bool = os.getenv("GEMINI_CONTEXT_CACHE_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    gemini_context_cache_min_chars: int = _int_env("GEMINI_CONTEXT_CACHE_MIN_CHARS", 6000)
    gemini_context_cache_ttl_seconds: int = _int_env("GEMINI_CONTEXT_CACHE_TTL_SECONDS", 300)
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    vault_path: Path = Path(os.getenv("OBSIDIAN_VAULT_PATH", "vault_output"))
    default_mode: str = os.getenv("SPECS_FIRST_MODE", "mock")

    collection_min_interval_seconds: float = _float_env("COLLECTION_MIN_INTERVAL_SECONDS", 1.0)
    bilibili_comment_page_delay_seconds: float = _float_env("BILIBILI_COMMENT_PAGE_DELAY_SECONDS", 3.0)
    youtube_comment_delay_min: float = _float_env("YOUTUBE_COMMENT_DELAY_MIN", 1.0)
    youtube_comment_delay_max: float = _float_env("YOUTUBE_COMMENT_DELAY_MAX", 3.0)
    youtube_comment_max_per_video: int = _int_env("YOUTUBE_COMMENT_MAX_PER_VIDEO", 20)
    bilibili_max_videos_per_sku: int = _int_env("BILIBILI_MAX_VIDEOS_PER_SKU", 2)
    bilibili_max_comments_per_video: int = _int_env("BILIBILI_MAX_COMMENTS_PER_VIDEO", 50)
    bilibili_asr_fallback: bool = os.getenv("BILIBILI_ASR_FALLBACK", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    bilibili_sessdata: str = os.getenv("BILIBILI_SESSDATA", "")
    bilibili_bili_jct: str = os.getenv("BILIBILI_BILI_JCT", "")
    bilibili_dedeuserid: str = os.getenv("BILIBILI_DEDEUSERID", "")
    bilibili_buvid3: str = os.getenv("BILIBILI_BUVID3", "")

    taobao_cookie: str = os.getenv("TAOBAO_COOKIE", "")
    taobao_m_h5_tk: str = os.getenv("TAOBAO_M_H5_TK", "")

    jd_cookie: str = os.getenv("JD_COOKIE", "")

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def model_mode(self) -> str:
        if self.has_gemini and self.has_openai:
            return "hybrid"
        if self.has_gemini or self.has_openai:
            return "partial"
        return "keyword"


settings = Settings()
