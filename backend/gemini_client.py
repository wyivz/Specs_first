from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from collectors.settings import settings

# GA default per https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash
RECOMMENDED_GEMINI_MODEL = "gemini-3.5-flash"

RETIRED_GEMINI_MODELS: frozenset[str] = frozenset(
    {
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
        "gemini-1.5-flash-001",
        "gemini-1.5-flash-002",
        "gemini-1.5-pro",
        "gemini-1.5-pro-001",
        "gemini-1.5-pro-002",
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash-lite-001",
    }
)

RETIRED_GEMINI_MODEL_PREFIXES: tuple[str, ...] = ("gemini-1.5-", "gemini-2.0-")

# Gemini 3.x docs: avoid temperature/top_p/top_k; use thinking_level instead.
_TASK_PROFILES: dict[str, dict[str, Any]] = {
    "probe": {
        "thinking_level": "minimal",
        "max_output_tokens": 256,
    },
    "json_extract": {
        "thinking_level": "low",
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    },
    "vision_json": {
        "thinking_level": "low",
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    },
    "corpus_extract": {
        "thinking_level": "low",
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    },
}


def is_retired_gemini_model(model: str) -> bool:
    normalized = model.strip().lower()
    if not normalized:
        return False
    if normalized in RETIRED_GEMINI_MODELS:
        return True
    return any(normalized.startswith(prefix) for prefix in RETIRED_GEMINI_MODEL_PREFIXES)


def is_gemini_3_family(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith("gemini-3") or normalized.startswith("gemini-3.")


def resolve_gemini_model(model: str | None = None) -> str:
    raw = (model or settings.gemini_model).strip()
    if is_retired_gemini_model(raw):
        return RECOMMENDED_GEMINI_MODEL
    return raw or RECOMMENDED_GEMINI_MODEL


def build_generation_config(task: str, model: str | None = None) -> dict[str, Any]:
    """Build GenerateContent config aligned with Gemini 3.5 Flash guidance."""
    resolved = resolve_gemini_model(model)
    profile = dict(_TASK_PROFILES.get(task, _TASK_PROFILES["json_extract"]))
    thinking_level = (settings.gemini_thinking_level or profile.get("thinking_level", "low")).strip().lower()
    if task == "probe":
        thinking_level = "minimal"

    config: dict[str, Any] = {
        "max_output_tokens": int(profile.get("max_output_tokens", 8192)),
    }
    mime_type = profile.get("response_mime_type")
    if mime_type:
        config["response_mime_type"] = mime_type

    if is_gemini_3_family(resolved):
        config["thinking_config"] = {"thinking_level": thinking_level}
    return config


def extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    chunks: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "thought", False):
                continue
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(str(piece))
    return " ".join(chunks).strip()


@dataclass
class GeminiClient:
    """Thin wrapper around google-genai for Gemini 3.x production defaults."""

    _sdk_client: Any | None = field(default=None, init=False, repr=False)

    def client(self) -> Any:
        if self._sdk_client is None:
            from google import genai

            self._sdk_client = genai.Client(api_key=settings.gemini_api_key)
        return self._sdk_client

    def close(self) -> None:
        sdk = self._sdk_client
        self._sdk_client = None
        if sdk is None:
            return
        close = getattr(sdk, "close", None)
        if callable(close):
            close()

    def generate_text(
        self,
        contents: Any,
        *,
        task: str = "json_extract",
        model: str | None = None,
        system_instruction: str | None = None,
        cached_content: str | None = None,
    ) -> str:
        resolved = resolve_gemini_model(model)
        config = build_generation_config(task, resolved)
        if system_instruction:
            config["system_instruction"] = system_instruction
        if cached_content:
            config["cached_content"] = cached_content
        response = self.client().models.generate_content(
            model=resolved,
            contents=contents,
            config=config,
        )
        return extract_response_text(response)

    def generate_multimodal(
        self,
        parts: list[Any],
        *,
        task: str = "vision_json",
        model: str | None = None,
    ) -> str:
        resolved = resolve_gemini_model(model)
        config = build_generation_config(task, resolved)
        response = self.client().models.generate_content(
            model=resolved,
            contents=parts,
            config=config,
        )
        return extract_response_text(response)

    @contextmanager
    def cached_corpus(
        self,
        corpus_text: str,
        system_instruction: str,
        *,
        model: str | None = None,
    ) -> Iterator[str | None]:
        resolved = resolve_gemini_model(model)
        if not (
            settings.gemini_context_cache_enabled
            and len(corpus_text) >= settings.gemini_context_cache_min_chars
        ):
            yield None
            return

        cache = None
        client = None
        cache_name = ""
        try:
            client = self.client()
            cache = client.caches.create(
                model=resolved,
                config={
                    "contents": corpus_text,
                    "system_instruction": system_instruction,
                    "ttl": f"{max(60, int(settings.gemini_context_cache_ttl_seconds))}s",
                },
            )
            cache_name = getattr(cache, "name", "") or ""
            yield cache_name or None
        except Exception:
            yield None
        finally:
            if cache is not None and client is not None:
                try:
                    client.caches.delete(name=cache.name)
                except Exception:
                    pass


_default_client: GeminiClient | None = None


def get_gemini_client() -> GeminiClient:
    global _default_client
    if _default_client is None:
        _default_client = GeminiClient()
    return _default_client
