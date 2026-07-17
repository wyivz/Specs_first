from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Cookie / API keys that users often refresh without restarting Streamlit.
_CREDENTIAL_ENV_KEYS = frozenset(
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

# Always resolve against repo root (collectors/..) so Streamlit cwd cannot miss .env.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DOTENV_PATH = _PROJECT_ROOT / ".env"


def dotenv_path() -> Path:
    if _DOTENV_PATH.exists():
        return _DOTENV_PATH
    return Path.cwd() / ".env"


def _read_dotenv_values(*, path: Path | None = None) -> dict[str, str]:
    env_path = path or dotenv_path()
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_dotenv(*, overwrite_credentials: bool = False, overwrite_all: bool = False) -> None:
    values = _read_dotenv_values()
    if not values:
        return
    for key, value in values.items():
        if overwrite_all:
            os.environ[key] = value
            continue
        if not value:
            continue
        existing = os.environ.get(key)
        if overwrite_credentials and key in _CREDENTIAL_ENV_KEYS:
            os.environ[key] = value
        elif existing is None or (key in _CREDENTIAL_ENV_KEYS and not existing.strip()):
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _str_env(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    return raw or default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_load_dotenv()


def _build_settings() -> Settings:
    """Construct Settings from the current process environment."""
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        openai_model=os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4o-mini"),
        gemini_model=os.getenv("DEFAULT_GEMINI_MODEL", "gemini-3.5-flash"),
        gemini_context_cache_enabled=os.getenv("GEMINI_CONTEXT_CACHE_ENABLED", "true").strip().lower()
        not in {"0", "false", "no"},
        gemini_context_cache_min_chars=_int_env("GEMINI_CONTEXT_CACHE_MIN_CHARS", 6000),
        gemini_context_cache_ttl_seconds=_int_env("GEMINI_CONTEXT_CACHE_TTL_SECONDS", 300),
        gemini_call_timeout_seconds=_float_env("GEMINI_CALL_TIMEOUT_SECONDS", 45.0),
        gemini_thinking_level=os.getenv("GEMINI_THINKING_LEVEL", "").strip(),
        redis_url=os.getenv("REDIS_URL", "").strip(),
        vault_path=Path(os.getenv("OBSIDIAN_VAULT_PATH", "vault_output")),
        default_mode=os.getenv("SPECS_FIRST_MODE", "mock"),
        collection_min_interval_seconds=_float_env("COLLECTION_MIN_INTERVAL_SECONDS", 1.0),
        ecommerce_min_interval_seconds=_float_env("ECOMMERCE_MIN_INTERVAL_SECONDS", 3.0),
        ecommerce_max_urls_per_platform=_int_env("ECOMMERCE_MAX_URLS_PER_PLATFORM", 2),
        ecommerce_collect_timeout_seconds=_float_env("ECOMMERCE_COLLECT_TIMEOUT_SECONDS", 300.0),
        collection_parallel_platforms=os.getenv("COLLECTION_PARALLEL_PLATFORMS", "true").strip().lower()
        not in {"0", "false", "no"},
        bilibili_comment_page_delay_seconds=_float_env("BILIBILI_COMMENT_PAGE_DELAY_SECONDS", 3.0),
        youtube_comment_delay_min=_float_env("YOUTUBE_COMMENT_DELAY_MIN", 1.0),
        youtube_comment_delay_max=_float_env("YOUTUBE_COMMENT_DELAY_MAX", 3.0),
        youtube_comment_max_per_video=_int_env("YOUTUBE_COMMENT_MAX_PER_VIDEO", 20),
        youtube_asr_fallback=os.getenv("YOUTUBE_ASR_FALLBACK", "false").strip().lower() not in {"0", "false", "no"},
        bilibili_max_videos_per_sku=_int_env("BILIBILI_MAX_VIDEOS_PER_SKU", 2),
        bilibili_max_comments_per_video=_int_env("BILIBILI_MAX_COMMENTS_PER_VIDEO", 50),
        bilibili_asr_fallback=os.getenv("BILIBILI_ASR_FALLBACK", "true").strip().lower() not in {"0", "false", "no"},
        asr_max_audio_seconds=_int_env("ASR_MAX_AUDIO_SECONDS", 600),
        bilibili_sessdata=os.getenv("BILIBILI_SESSDATA", ""),
        bilibili_bili_jct=os.getenv("BILIBILI_BILI_JCT", ""),
        bilibili_dedeuserid=os.getenv("BILIBILI_DEDEUSERID", ""),
        bilibili_buvid3=os.getenv("BILIBILI_BUVID3", ""),
        taobao_cookie=os.getenv("TAOBAO_COOKIE", ""),
        taobao_m_h5_tk=os.getenv("TAOBAO_M_H5_TK", ""),
        jd_cookie=os.getenv("JD_COOKIE", ""),
        youtube_cookie=os.getenv("YOUTUBE_COOKIE", ""),
        youtube_browser_transcript=os.getenv("YOUTUBE_BROWSER_TRANSCRIPT", "true").strip().lower()
        not in {"0", "false", "no"},
        reddit_cookie=os.getenv("REDDIT_COOKIE", ""),
        smoke_jd_url=_str_env("SMOKE_JD_URL", "https://item.jd.com/100012043978.html"),
        smoke_taobao_item_id=_str_env("SMOKE_TAOBAO_ITEM_ID", "520813140663"),
        smoke_bilibili_bvid=os.getenv("SMOKE_BILIBILI_BVID", "").strip(),
        smoke_youtube_url=_str_env("SMOKE_YOUTUBE_URL", "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
        collection_trace_enabled=os.getenv("COLLECTION_TRACE", "true").strip().lower() not in {"0", "false", "no"},
        collection_trace_dir=Path(os.getenv("COLLECTION_TRACE_DIR", "vault_output")),
    )


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_model: str = os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4o-mini")
    gemini_model: str = os.getenv("DEFAULT_GEMINI_MODEL", "gemini-3.5-flash")

    gemini_context_cache_enabled: bool = os.getenv("GEMINI_CONTEXT_CACHE_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    gemini_context_cache_min_chars: int = _int_env("GEMINI_CONTEXT_CACHE_MIN_CHARS", 6000)
    gemini_context_cache_ttl_seconds: int = _int_env("GEMINI_CONTEXT_CACHE_TTL_SECONDS", 300)
    gemini_call_timeout_seconds: float = _float_env("GEMINI_CALL_TIMEOUT_SECONDS", 45.0)
    # Gemini 3.x thinking level: minimal | low | medium | high (see Gemini 3.5 Flash docs)
    gemini_thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "").strip()
    # Empty by default: probing a down local Redis blocked GUI cold-start for seconds.
    # Set REDIS_URL explicitly when a shared checkpoint store is required.
    redis_url: str = os.getenv("REDIS_URL", "").strip()
    vault_path: Path = Path(os.getenv("OBSIDIAN_VAULT_PATH", "vault_output"))
    default_mode: str = os.getenv("SPECS_FIRST_MODE", "mock")

    collection_min_interval_seconds: float = _float_env("COLLECTION_MIN_INTERVAL_SECONDS", 1.0)
    ecommerce_min_interval_seconds: float = _float_env("ECOMMERCE_MIN_INTERVAL_SECONDS", 3.0)
    ecommerce_max_urls_per_platform: int = _int_env("ECOMMERCE_MAX_URLS_PER_PLATFORM", 2)
    ecommerce_collect_timeout_seconds: float = _float_env("ECOMMERCE_COLLECT_TIMEOUT_SECONDS", 300.0)
    collection_parallel_platforms: bool = os.getenv("COLLECTION_PARALLEL_PLATFORMS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    bilibili_comment_page_delay_seconds: float = _float_env("BILIBILI_COMMENT_PAGE_DELAY_SECONDS", 3.0)
    youtube_comment_delay_min: float = _float_env("YOUTUBE_COMMENT_DELAY_MIN", 1.0)
    youtube_comment_delay_max: float = _float_env("YOUTUBE_COMMENT_DELAY_MAX", 3.0)
    youtube_comment_max_per_video: int = _int_env("YOUTUBE_COMMENT_MAX_PER_VIDEO", 20)
    youtube_asr_fallback: bool = os.getenv("YOUTUBE_ASR_FALLBACK", "false").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    bilibili_max_videos_per_sku: int = _int_env("BILIBILI_MAX_VIDEOS_PER_SKU", 2)
    bilibili_max_comments_per_video: int = _int_env("BILIBILI_MAX_COMMENTS_PER_VIDEO", 50)
    bilibili_asr_fallback: bool = os.getenv("BILIBILI_ASR_FALLBACK", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    # Cap local ASR audio length (seconds). Long review videos otherwise hang CPU for ages.
    asr_max_audio_seconds: int = _int_env("ASR_MAX_AUDIO_SECONDS", 600)

    bilibili_sessdata: str = os.getenv("BILIBILI_SESSDATA", "")
    bilibili_bili_jct: str = os.getenv("BILIBILI_BILI_JCT", "")
    bilibili_dedeuserid: str = os.getenv("BILIBILI_DEDEUSERID", "")
    bilibili_buvid3: str = os.getenv("BILIBILI_BUVID3", "")

    taobao_cookie: str = os.getenv("TAOBAO_COOKIE", "")
    taobao_m_h5_tk: str = os.getenv("TAOBAO_M_H5_TK", "")

    jd_cookie: str = os.getenv("JD_COOKIE", "")

    # YouTube cookies — optional; improves transcript/PoToken success in browser fetches
    youtube_cookie: str = os.getenv("YOUTUBE_COOKIE", "")
    youtube_browser_transcript: bool = os.getenv("YOUTUBE_BROWSER_TRANSCRIPT", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    # Reddit session cookies — required for auto forum search on reddit.com
    reddit_cookie: str = os.getenv("REDDIT_COOKIE", "")

    # Smoke / probe defaults (override in .env; leave Bilibili empty to skip API smoke)
    smoke_jd_url: str = _str_env("SMOKE_JD_URL", "https://item.jd.com/100012043978.html")
    smoke_taobao_item_id: str = _str_env("SMOKE_TAOBAO_ITEM_ID", "520813140663")
    smoke_bilibili_bvid: str = os.getenv("SMOKE_BILIBILI_BVID", "").strip()
    smoke_youtube_url: str = _str_env(
        "SMOKE_YOUTUBE_URL",
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    )

    collection_trace_enabled: bool = os.getenv("COLLECTION_TRACE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    collection_trace_dir: Path = Path(os.getenv("COLLECTION_TRACE_DIR", "vault_output"))

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


def reload_settings(*, overwrite_all: bool = True) -> Settings:
    """Re-read .env, refresh os.environ, and rebuild the module-level settings singleton."""
    global settings
    _load_dotenv(overwrite_all=overwrite_all)
    settings = _build_settings()
    return settings


def reload_credential_env() -> None:
    """Re-read cookie/API keys from .env so health/UI pick up updates without restart."""
    reload_settings(overwrite_all=True)


settings = _build_settings()
