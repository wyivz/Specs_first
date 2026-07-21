from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from collectors.discovery import discover_skus_from_evidence
from collectors.http import HttpClient, SearchResult
from collectors.sources.official import OfficialSourceCollector, _english_discovery_alias


class DiscoverySearchFallbackTest(unittest.TestCase):
    def test_quick_search_still_tries_ddgs_when_html_empty(self) -> None:
        client = HttpClient(timeout_seconds=2)

        def empty_html(self, query, max_results):  # noqa: ANN001
            return []

        def empty_lite(self, query, max_results):  # noqa: ANN001
            return []

        def fake_ddgs(query, max_results):  # noqa: ANN001
            return [SearchResult("Sony A7 IV", "https://example.com/a7iv", "full-frame")]

        with (
            patch.object(HttpClient, "_search_duckduckgo_html", empty_html),
            patch.object(HttpClient, "_search_duckduckgo_lite", empty_lite),
            patch.object(HttpClient, "_search_duckduckgo_ddgs", staticmethod(fake_ddgs)),
        ):
            hits = client.search("索尼全画幅相机", max_results=5, quick=True)
        self.assertEqual(len(hits), 1)
        self.assertIn("A7", hits[0].title)

    def test_english_alias_is_category_agnostic(self) -> None:
        alias = _english_discovery_alias("罗技无线鼠标")
        self.assertIn("Logitech", alias)
        self.assertIn("mouse", alias)
        self.assertNotRegex(alias, r"[\u4e00-\u9fff]")

    def test_discovery_hits_include_english_plan(self) -> None:
        seen: list[str] = []

        class _Http:
            def search(self, query, max_results=8, *, quick=False):
                seen.append(query)
                if "Logitech" in query and "mouse" in query:
                    return [SearchResult("Logitech G304 review", "https://ex/a", "wireless")]
                return []

        collector = OfficialSourceCollector(_Http())  # type: ignore[arg-type]
        hits = collector.collect_discovery_hits("罗技无线鼠标", "Product", quick=True)
        self.assertTrue(any("Logitech" in q for q in seen))
        self.assertEqual(len(hits), 1)

    def test_auto_wires_llm_when_callback_missing(self) -> None:
        hits = [SearchResult("2024 best wireless mice roundup", "https://ex/a", "includes G304")]

        def fake_llm(_system: str, _prompt: str) -> dict[str, Any]:
            return {"products": [{"sku": "Logitech G304", "brand": "Logitech", "evidence_index": 1}]}

        with patch("backend.discovery_llm.create_discover_llm_json", return_value=fake_llm):
            with patch("collectors.discovery.settings") as fake_settings:
                fake_settings.has_gemini = True
                fake_settings.has_openai = False
                candidates = discover_skus_from_evidence(
                    "无线鼠标",
                    hits,
                    llm_json=None,
                    fetch_bodies=False,
                )
        self.assertEqual([c.sku for c in candidates], ["Logitech G304"])


if __name__ == "__main__":
    unittest.main()
