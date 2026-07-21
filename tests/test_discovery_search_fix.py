from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from collectors.discovery import discover_skus_from_evidence, expand_discovery_search_plans
from collectors.http import HttpClient, SearchResult
from collectors.sources.official import OfficialSourceCollector


class DiscoverySearchFallbackTest(unittest.TestCase):
    def test_quick_search_still_tries_ddgs_when_html_empty(self) -> None:
        client = HttpClient(timeout_seconds=2)

        def empty_html(self, query, max_results):  # noqa: ANN001
            return []

        def empty_lite(self, query, max_results):  # noqa: ANN001
            return []

        def fake_ddgs(query, max_results):  # noqa: ANN001
            return [SearchResult("Logitech G304", "https://example.com/g304", "wireless")]

        with (
            patch.object(HttpClient, "_search_duckduckgo_html", empty_html),
            patch.object(HttpClient, "_search_duckduckgo_lite", empty_lite),
            patch.object(HttpClient, "_search_duckduckgo_ddgs", staticmethod(fake_ddgs)),
        ):
            hits = client.search("无线鼠标", max_results=5, quick=True)
        self.assertEqual(len(hits), 1)
        self.assertIn("G304", hits[0].title)

    def test_expand_search_plans_uses_structured_llm(self) -> None:
        def fake_llm(_system: str, _prompt: str) -> dict[str, Any]:
            return {
                "search_queries": [
                    "Logitech wireless mouse models",
                    "罗技 无线鼠标 型号 对比",
                ]
            }

        plans = expand_discovery_search_plans(
            "无线鼠标",
            "Product",
            quick=True,
            llm_json=fake_llm,
            max_plans=5,
        )
        self.assertIn("无线鼠标", plans)
        self.assertTrue(any("Logitech" in p for p in plans))
        self.assertTrue(any("罗技" in p for p in plans))

    def test_collect_hits_honors_llm_search_plans(self) -> None:
        seen: list[str] = []

        class _Http:
            def search(self, query, max_results=8, *, quick=False):
                seen.append(query)
                if "Logitech wireless mouse models" in query:
                    return [SearchResult("Logitech G304 review", "https://ex/a", "wireless")]
                return []

        collector = OfficialSourceCollector(_Http())  # type: ignore[arg-type]
        hits = collector.collect_discovery_hits(
            "无线鼠标",
            "Product",
            quick=True,
            search_plans=["无线鼠标", "Logitech wireless mouse models"],
        )
        self.assertEqual(seen, ["无线鼠标", "Logitech wireless mouse models"])
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
