from __future__ import annotations

from collections.abc import Callable
from typing import Any

from collectors.settings import settings


def create_discover_llm_json() -> Callable[[str, str], dict[str, Any]]:
    """LLM callback for SKU discovery — wired from backend into RealCollector."""

    def _llm_json(system: str, prompt: str) -> dict[str, Any]:
        if settings.has_gemini:
            return _gemini_discover(system, prompt)
        return _openai_discover(system, prompt)

    return _llm_json


def wire_discover_llm(collector: object) -> None:
    """Attach discovery LLM to a RealCollector when API keys are configured."""
    if not (settings.has_gemini or settings.has_openai):
        return
    setattr(collector, "_discover_llm_json", create_discover_llm_json())


def _gemini_discover(system: str, prompt: str) -> dict[str, Any]:
    from backend.gemini_client import get_gemini_client
    from backend.router_schemas import parse_json_payload

    text = get_gemini_client().generate_text(
        prompt,
        task="json_extract",
        system_instruction=system,
    )
    return parse_json_payload(text or "", default={"products": []})


def _openai_discover(system: str, prompt: str) -> dict[str, Any]:
    from openai import OpenAI

    from backend.router_schemas import parse_json_payload

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    text = (response.choices[0].message.content or "") if response.choices else ""
    return parse_json_payload(text or "", default={"products": []})
