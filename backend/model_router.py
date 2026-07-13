from __future__ import annotations

from backend.config import settings
from backend.router_hybrid import HybridModelRouter
from backend.router_keyword import KeywordModelRouter
from backend.router_schemas import (
    ARBITRATION_SCHEMA,
    FINDINGS_SCHEMA,
    _parse_json_payload,
    parse_json_payload,
)

__all__ = [
    "ARBITRATION_SCHEMA",
    "FINDINGS_SCHEMA",
    "HybridModelRouter",
    "KeywordModelRouter",
    "ModelRouter",
    "create_model_router",
    "_parse_json_payload",
    "parse_json_payload",
]


def create_model_router(mode: str | None = None) -> KeywordModelRouter:
    resolved = (mode or settings.model_mode or "keyword").strip().lower()
    # keyword = deterministic only; never call Gemini/OpenAI even if keys exist.
    if resolved == "keyword":
        return KeywordModelRouter()
    if resolved in {"hybrid", "partial"} and (settings.has_gemini or settings.has_openai):
        return HybridModelRouter(resolved)
    return KeywordModelRouter()


ModelRouter = create_model_router
