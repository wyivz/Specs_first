from __future__ import annotations

from dataclasses import dataclass

from collectors.settings import settings
from backend.gemini_client import (
    RECOMMENDED_GEMINI_MODEL,
    get_gemini_client,
    is_retired_gemini_model,
    resolve_gemini_model,
)

__all__ = [
    "RECOMMENDED_GEMINI_MODEL",
    "GeminiHealthStatus",
    "build_gemini_health",
    "is_retired_gemini_model",
    "resolve_gemini_model",
]


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
            f"Model '{model}' is retired. Set DEFAULT_GEMINI_MODEL={RECOMMENDED_GEMINI_MODEL} in .env."
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
        from google import genai  # noqa: F401
    except ImportError:
        return False, "google-genai is not installed (required for gemini-3.5-flash)"

    try:
        text = get_gemini_client().generate_text(
            "Reply with exactly: ok",
            task="probe",
            model=model,
        )
    except Exception as exc:
        lowered = str(exc).lower()
        if "not found" in lowered or "404" in lowered or "does not exist" in lowered:
            return False, f"Model '{model}' is unavailable: {exc}"
        return False, str(exc)

    if text:
        return True, ""
    return False, f"Model '{model}' returned an empty response (check thinking_level / output budget)"
