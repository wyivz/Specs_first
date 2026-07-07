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


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_model: str = os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4o-mini")
    gemini_model: str = os.getenv("DEFAULT_GEMINI_MODEL", "gemini-1.5-flash")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    vault_path: Path = Path(os.getenv("OBSIDIAN_VAULT_PATH", "vault_output"))
    default_mode: str = os.getenv("SPECS_FIRST_MODE", "mock")

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
