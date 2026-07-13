from __future__ import annotations

import json
import re
from typing import Any

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "condition": {"type": "string"},
                    "frequency": {"type": "string"},
                    "severity": {"type": "string", "enum": ["minor", "major"]},
                    "evidence_index": {"type": "integer"},
                },
                "required": ["title", "detail", "condition", "frequency", "severity", "evidence_index"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

ARBITRATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "warnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "official_claim": {"type": "string"},
                    "real_world_claim": {"type": "string"},
                    "level": {"type": "string", "enum": ["minor", "major"]},
                    "arbitration_summary": {"type": "string"},
                    "finding_index": {"type": "integer"},
                },
                "required": [
                    "field",
                    "official_claim",
                    "real_world_claim",
                    "level",
                    "arbitration_summary",
                    "finding_index",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["warnings", "summary"],
    "additionalProperties": False,
}

CATEGORY_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category_label": {"type": "string"},
        "slots": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 5,
            "maxItems": 8,
        },
        "aliases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string"},
                    "slot": {"type": "string"},
                },
                "required": ["alias", "slot"],
                "additionalProperties": False,
            },
        },
        "comparison_keywords": {
            "type": "array",
            "items": {"type": "string"},
        },
        "search_modifiers": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "category_label",
        "slots",
        "aliases",
        "comparison_keywords",
        "search_modifiers",
    ],
    "additionalProperties": False,
}


def parse_json_payload(text: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = default if default is not None else {}
    text = (text or "").strip()
    if not text:
        return fallback
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return fallback
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return fallback


_parse_json_payload = parse_json_payload
