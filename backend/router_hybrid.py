from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.config import settings
from backend.retry import retry_call
from backend.router_keyword import KeywordModelRouter
from backend.router_schemas import ARBITRATION_SCHEMA, parse_json_payload
from collectors.extractors import ParsedPrice
from schemas import ConflictLevel, ConflictWarning, EvidenceItem, OfficialSpec, PriceFinding, RealWorldFinding

class HybridModelRouter(KeywordModelRouter):
    """Gemini: massive text ingestion + multimodal OCR. OpenAI: structured output only."""

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode or settings.model_mode
        self._keyword = KeywordModelRouter()
        self._last_summary = ""

    def extract_official_specs_from_text(
        self,
        sku: str,
        text: str,
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        if settings.has_gemini and text.strip():
            try:
                specs, highlights = self._gemini_extract_official_specs(sku, text, source_url, category)
                if specs:
                    return specs, highlights
            except Exception:
                pass
        return self._keyword.extract_official_specs_from_text(sku, text, source_url, category)

    def extract_real_world_findings(self, sku: str, corpus: list[EvidenceItem]) -> list[RealWorldFinding]:
        if not corpus:
            return []
        if settings.has_gemini:
            try:
                findings = self._gemini_extract_findings(sku, corpus)
                if findings:
                    return findings
            except Exception:
                pass
        return self._keyword.extract_real_world_findings(sku, corpus)

    def extract_official_specs_from_images(
        self,
        sku: str,
        image_urls: list[str],
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        if not (settings.has_gemini and image_urls):
            return [], []
        try:
            return self._gemini_extract_official_specs_from_images(sku, image_urls, source_url, category)
        except Exception:
            return [], []

    def enrich_prices_with_ocr(self, sku: str, prices: list[PriceFinding]) -> list[PriceFinding]:
        if not settings.has_gemini:
            return prices
        enriched: list[PriceFinding] = []
        for price in prices:
            screenshot_paths = self._resolve_screenshot_paths(price.screenshot_path)
            if not screenshot_paths:
                enriched.append(price)
                continue
            parsed_candidates: list[ParsedPrice] = []
            for screenshot in screenshot_paths:
                if not screenshot.exists():
                    continue
                try:
                    parsed = retry_call(
                        lambda shot=screenshot: self._gemini_ocr_price(sku, shot, price.evidence.url),
                        attempts=2,
                    )
                except Exception:
                    continue
                if parsed:
                    parsed_candidates.append(parsed)
            if not parsed_candidates:
                enriched.append(price)
                continue
            best = min(parsed_candidates, key=lambda item: item.final_price)
            enriched.append(
                PriceFinding(
                    platform=price.platform,
                    list_price=best.list_price,
                    coupon_discount=best.coupon_discount,
                    subsidy_discount=best.subsidy_discount,
                    cross_store_discount=best.cross_store_discount,
                    final_price=best.final_price,
                    screenshot_path=price.screenshot_path,
                    captured_at=price.captured_at,
                    evidence=price.evidence,
                )
            )
        return enriched or prices

    @staticmethod
    def _resolve_screenshot_paths(raw: str) -> list[Path]:
        if not raw:
            return []
        paths = [Path(part.strip()) for part in raw.split(",") if part.strip()]
        if paths:
            return paths
        single = Path(raw)
        if single.exists():
            return [single]
        if single.parent.exists():
            return sorted(single.parent.glob(f"{single.stem}*.png"))
        return []

    def arbitrate_conflicts(
        self,
        findings: list[RealWorldFinding],
        official_specs: list[OfficialSpec] | None = None,
    ) -> list[ConflictWarning]:
        self._last_summary = ""
        if settings.has_openai and findings:
            try:
                warnings = retry_call(
                    lambda: self._openai_arbitrate_structured(findings, official_specs or []),
                    attempts=2,
                )
                if warnings is not None:
                    return warnings
            except Exception:
                pass
        return self._keyword.arbitrate_conflicts(findings, official_specs)

    def summarize(self, warnings: list[ConflictWarning], findings: list[RealWorldFinding]) -> str:
        if self._last_summary:
            return self._last_summary
        return self._keyword.summarize(warnings, findings)

    def _gemini_model(self):
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        return genai.GenerativeModel(settings.gemini_model)

    @contextmanager
    def _gemini_cached_content(self, corpus_text: str, system_instruction: str) -> Iterator[Any]:
        """Yield a GenerativeModel bound to a Gemini context cache of ``corpus_text``.

        Context caching lets large, repeatedly-referenced text (the same
        product corpus is re-sent on every retry attempt) be billed once
        instead of on every call. Gemini enforces a minimum cache size
        (~2048 tokens), so caching is only attempted above
        ``gemini_context_cache_min_chars``. Any failure (unsupported model,
        below the token floor, API error) silently falls back to yielding
        ``None`` so the caller sends the full prompt inline instead. The
        remote cache is always cleaned up on exit.
        """
        cache = None
        model = None
        if settings.gemini_context_cache_enabled and len(corpus_text) >= settings.gemini_context_cache_min_chars:
            try:
                import datetime

                import google.generativeai as genai
                from google.generativeai import caching

                genai.configure(api_key=settings.gemini_api_key)
                cache = caching.CachedContent.create(
                    model=settings.gemini_model,
                    system_instruction=system_instruction,
                    contents=[corpus_text],
                    ttl=datetime.timedelta(seconds=settings.gemini_context_cache_ttl_seconds),
                )
                model = genai.GenerativeModel.from_cached_content(cached_content=cache)
            except Exception:
                cache = None
                model = None
        try:
            yield model
        finally:
            if cache is not None:
                try:
                    cache.delete()
                except Exception:
                    pass

    def _gemini_extract_official_specs(
        self,
        sku: str,
        text: str,
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        from schemas.category_profile import canonical_slots

        corpus = text[:120000]
        slots = canonical_slots(category)
        system_instruction = (
            "You extract official product specifications verbatim from source text. "
            "Never invent values that are not present in the text."
        )
        instruction = (
            f"Extract official product specifications for {sku} (category: {category or 'unspecified'}) "
            "from the source text. "
            "Return JSON only: "
            '{"specs":[{"name":"snake_case_parameter_name","value":"","unit":""}],'
            '"highlights":["unique feature"]}. '
            f"Prefer these canonical snake_case field names when the source text covers them: {', '.join(slots)}. "
            "For any other factual attribute present in the text that doesn't fit those names, "
            "invent a concise snake_case name (for example screen_size, battery_capacity) instead of dropping it. "
            "Attributes unique to this SKU that don't belong as a comparison column go into 'highlights' instead. "
            "Include only factual values present in the text."
        )

        with self._gemini_cached_content(corpus, system_instruction) as cached_model:
            def _call():
                if cached_model is not None:
                    return cached_model.generate_content(instruction)
                return self._gemini_model().generate_content(f"{instruction}\n\n{corpus}")

            response = retry_call(_call, attempts=2)
        payload = parse_json_payload(response.text or "", default={"specs": [], "highlights": []})
        specs = [
            OfficialSpec(
                name=str(item.get("name", "")).strip(),
                value=str(item.get("value", "")).strip(),
                unit=str(item.get("unit", "")).strip(),
                source_url=source_url,
            )
            for item in payload.get("specs", [])
            if item.get("name") and item.get("value")
        ]
        highlights = [str(item).strip() for item in payload.get("highlights", []) if str(item).strip()]
        return specs, highlights[:5]

    def _gemini_extract_findings(self, sku: str, corpus: list[EvidenceItem]) -> list[RealWorldFinding]:
        corpus_text = "\n\n".join(
            f"[{index}] platform={item.platform} author={item.author} url={item.url}\n{item.excerpt}"
            for index, item in enumerate(corpus)
        )
        system_instruction = (
            "You are a harsh product QA reviewer. Discard marketing fluff and subjective praise; "
            "extract only evidence-backed real-world flaws from the provided corpus."
        )
        instruction = (
            f"Review the cached corpus for {sku}. "
            "Return JSON only with shape "
            '{"findings":[{"title":"","detail":"","condition":"","frequency":"","severity":"minor|major","evidence_index":0}]}. '
            "Each finding must reference one evidence_index from the corpus."
        )

        with self._gemini_cached_content(corpus_text, system_instruction) as cached_model:
            def _call():
                if cached_model is not None:
                    return cached_model.generate_content(instruction)
                return self._gemini_model().generate_content(f"{instruction}\n\n{corpus_text}")

            response = retry_call(_call, attempts=2)
        payload = parse_json_payload(response.text or "", default={"findings": []})
        findings: list[RealWorldFinding] = []
        for item in payload.get("findings", []):
            index = int(item.get("evidence_index", -1))
            if index < 0 or index >= len(corpus):
                continue
            findings.append(
                RealWorldFinding(
                    title=str(item.get("title", "Real-world issue")).strip(),
                    detail=str(item.get("detail", "")).strip() or corpus[index].excerpt,
                    condition=str(item.get("condition", "unspecified")).strip(),
                    frequency=str(item.get("frequency", "field report")).strip(),
                    severity=ConflictLevel(str(item.get("severity", "minor"))),
                    evidence=[corpus[index]],
                )
            )
        return findings

    def _gemini_ocr_price(self, sku: str, screenshot: Path, source_url: str) -> ParsedPrice | None:
        prompt = (
            f"Read this e-commerce product page screenshot for {sku}. "
            "Extract list price, coupon discount, subsidy discount, cross-store discount, and final checkout price in CNY. "
            "Return JSON only: "
            '{"list_price":0,"coupon_discount":0,"subsidy_discount":0,"cross_store_discount":0,"final_price":0}. '
            f"Source URL for context: {source_url}"
        )
        response = self._gemini_model().generate_content(
            [
                prompt,
                {"mime_type": "image/png", "data": screenshot.read_bytes()},
            ]
        )
        payload = parse_json_payload(response.text or "", default={})
        final_price = float(payload.get("final_price", 0) or 0)
        if final_price <= 0:
            return None
        return ParsedPrice(
            list_price=float(payload.get("list_price", final_price)),
            coupon_discount=float(payload.get("coupon_discount", 0)),
            subsidy_discount=float(payload.get("subsidy_discount", 0)),
            cross_store_discount=float(payload.get("cross_store_discount", 0)),
            final_price=final_price,
        )

    def _gemini_extract_official_specs_from_images(
        self,
        sku: str,
        image_urls: list[str],
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        from schemas.category_profile import canonical_slots
        from urllib.request import Request, urlopen

        slots = canonical_slots(category)
        prompt = (
            f"Extract product specifications for {sku} from these detail images. "
            "Return JSON only: "
            '{"specs":[{"name":"snake_case_parameter_name","value":"","unit":""}],"highlights":[""]}. '
            f"Prefer canonical fields: {', '.join(slots)}. Do not invent values."
        )
        specs_by_name: dict[str, OfficialSpec] = {}
        highlights: list[str] = []
        for url in image_urls[:8]:
            request = Request(url, headers={"User-Agent": "SpecsFirst/0.1"})
            with urlopen(request, timeout=12) as response:
                data = response.read(4_000_000)
                mime_type = response.headers.get_content_type() or "image/jpeg"
            gemini_response = self._gemini_model().generate_content(
                [prompt, {"mime_type": mime_type, "data": data}]
            )
            payload = parse_json_payload(gemini_response.text or "", default={"specs": [], "highlights": []})
            for item in payload.get("specs", []):
                name = str(item.get("name", "")).strip()
                value = str(item.get("value", "")).strip()
                if not (name and value):
                    continue
                specs_by_name.setdefault(
                    name,
                    OfficialSpec(
                        name=name,
                        value=value,
                        unit=str(item.get("unit", "")).strip(),
                        source_url=source_url,
                    ),
                )
            for item in payload.get("highlights", []):
                text = str(item).strip()
                if text and text not in highlights and len(highlights) < 5:
                    highlights.append(text)
        return list(specs_by_name.values()), highlights

    def _openai_arbitrate_structured(
        self,
        findings: list[RealWorldFinding],
        official_specs: list[OfficialSpec],
    ) -> list[ConflictWarning] | None:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        payload = {
            "findings": [
                {
                    "title": finding.title,
                    "detail": finding.detail,
                    "severity": finding.severity.value,
                    "evidence_urls": [item.url for item in finding.evidence],
                }
                for finding in findings
            ],
            "official_specs": [
                {"name": spec.name, "value": spec.value, "source_url": spec.source_url}
                for spec in official_specs
            ],
        }
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Arbitrate conflicts between official specs and real-world reports. "
                        "Use OpenAI structured output only; do not add prose outside JSON."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "arbitration", "strict": True, "schema": ARBITRATION_SCHEMA},
            },
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        warnings: list[ConflictWarning] = []
        for item in parsed.get("warnings", []):
            index = int(item.get("finding_index", -1))
            if index < 0 or index >= len(findings):
                continue
            finding = findings[index]
            warnings.append(
                ConflictWarning(
                    field=str(item.get("field", "general")),
                    official_claim=str(item.get("official_claim", "")),
                    real_world_claim=str(item.get("real_world_claim", finding.detail)),
                    level=ConflictLevel(str(item.get("level", finding.severity.value))),
                    arbitration_summary=str(item.get("arbitration_summary", "")),
                    evidence=finding.evidence,
                )
            )
        self._last_summary = str(parsed.get("summary", ""))
        return warnings
