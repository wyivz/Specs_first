from __future__ import annotations

from dataclasses import dataclass

from backend.config import settings

# Models shut down per https://ai.google.dev/gemini-api/docs/deprecations (2026-07).
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

RECOMMENDED_GEMINI_MODEL = "gemini-2.5-flash"


def resolve_gemini_model(model: str | None = None) -> str:
    """Return a live Gemini model id, upgrading known-retired defaults."""
    raw = (model or settings.gemini_model).strip()
    if is_retired_gemini_model(raw):
        return RECOMMENDED_GEMINI_MODEL
    return raw or RECOMMENDED_GEMINI_MODEL


def is_retired_gemini_model(model: str) -> bool:
    normalized = model.strip().lower()
    if not normalized:
        return False
    if normalized in RETIRED_GEMINI_MODELS:
        return True
    return any(normalized.startswith(prefix) for prefix in RETIRED_GEMINI_MODEL_PREFIXES)


@dataclass(frozen=True)
class GeminiHealthStatus:
    model: str
    api_key_configured: bool
    model_retired: bool
    recommended_model: str
    live_probe_ok: bool | None
    message: str

    @property
    def healthy(self) -> bool:
        if not self.api_key_configured:
            return True
        if self.model_retired:
            return False
        if self.live_probe_ok is False:
            return False
        return True


def build_gemini_health(*, live_probe: bool = False) -> GeminiHealthStatus:
    model = settings.gemini_model
    effective = resolve_gemini_model(model)
    api_key_configured = settings.has_gemini
    model_retired = is_retired_gemini_model(model)

    if model_retired:
        message = (
            f"Model '{model}' is retired. Set DEFAULT_GEMINI_MODEL={RECOMMENDED_GEMINI_MODEL} "
            "or gemini-3.5-flash in .env."
        )
        return GeminiHealthStatus(
            model=effective,
            api_key_configured=api_key_configured,
            model_retired=True,
            recommended_model=RECOMMENDED_GEMINI_MODEL,
            live_probe_ok=None if not live_probe else False,
            message=message,
        )

    if not api_key_configured:
        return GeminiHealthStatus(
            model=effective,
            api_key_configured=False,
            model_retired=False,
            recommended_model=RECOMMENDED_GEMINI_MODEL,
            live_probe_ok=None,
            message="GEMINI_API_KEY not set",
        )

    if not live_probe:
        return GeminiHealthStatus(
            model=effective,
            api_key_configured=True,
            model_retired=False,
            recommended_model=RECOMMENDED_GEMINI_MODEL,
            live_probe_ok=None,
            message="",
        )

    ok, message = _live_probe_gemini(effective)
    return GeminiHealthStatus(
        model=effective,
        api_key_configured=True,
        model_retired=False,
        recommended_model=RECOMMENDED_GEMINI_MODEL,
        live_probe_ok=ok,
        message=message,
    )


def _live_probe_gemini(model: str) -> tuple[bool, str]:
    try:
        import google.generativeai as genai
    except ImportError:
        return False, "google-generativeai is not installed"

    try:
        genai.configure(api_key=settings.gemini_api_key)
        response = genai.GenerativeModel(model).generate_content(
            "Reply with exactly: ok",
            generation_config={"max_output_tokens": 16, "temperature": 0},
        )
    except Exception as exc:
        lowered = str(exc).lower()
        if "not found" in lowered or "404" in lowered or "does not exist" in lowered:
            return False, f"Model '{model}' is unavailable: {exc}"
        return False, str(exc)

    text = (getattr(response, "text", None) or "").strip()
    if text:
        return True, ""
    return False, f"Model '{model}' returned an empty response"
