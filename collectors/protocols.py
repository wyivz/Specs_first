from __future__ import annotations

from typing import Protocol

from schemas import OfficialSpec


class SpecExtractionRouter(Protocol):
    """Minimal router surface used by source collectors for Gemini spec extraction."""

    def extract_official_specs_from_text(
        self,
        sku: str,
        text: str,
        source_url: str,
        *,
        category: str,
    ) -> tuple[list[OfficialSpec], list[str]]: ...

    def extract_official_specs_from_images(
        self,
        sku: str,
        images: list[str],
        source_url: str,
        *,
        category: str,
    ) -> tuple[list[OfficialSpec], list[str]]: ...
