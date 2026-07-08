from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.config import settings
from backend.retry import retry_call
from collectors.extractors import ParsedPrice
from schemas import ConflictLevel, ConflictWarning, EvidenceItem, OfficialSpec, PriceFinding, RealWorldFinding


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


class KeywordModelRouter:
    """Deterministic fallback when API keys are absent.
    
    NOTE: The keyword patterns below are EXAMPLES for common product issues.
    In production with real LLM APIs (Gemini/OpenAI), the extraction is done
    by the model based on the actual product context, not hardcoded patterns.
    This fallback exists only for testing without API keys.
    """

    def extract_official_specs_from_text(
        self,
        sku: str,
        text: str,
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        from collectors.extractors import extract_specs_from_text

        return extract_specs_from_text(text, source_url, category), []

    def extract_real_world_findings(self, sku: str, corpus: list[EvidenceItem]) -> list[RealWorldFinding]:
        """Extract real-world findings from corpus using keyword patterns.
        
        NOTE: These patterns are EXAMPLES for demonstration. In production,
        the Gemini model will dynamically identify issues based on product context.
        """
        findings: list[RealWorldFinding] = []
        seen_titles: set[str] = set()
        for evidence in corpus:
            text = evidence.excerpt.lower()
            finding: RealWorldFinding | None = None
            
            # Generic quality / performance issues
            if any(term in text for term in ["defect", "fail", "broken", "fault", "缺陷", "故障", "损坏"]):
                finding = RealWorldFinding(
                    title="Product defect report",
                    detail=evidence.excerpt,
                    condition="normal usage",
                    frequency="field report",
                    severity=ConflictLevel.MAJOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["quality control", "sample variation", "unit variation", "品控", "个体差异"]):
                finding = RealWorldFinding(
                    title="Quality control or sample variation",
                    detail=evidence.excerpt,
                    condition="sample-dependent",
                    frequency="field report",
                    severity=ConflictLevel.MAJOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["sticky", "damping", "卡顿", "延迟", "lag", "unresponsive", "slow"]):
                finding = RealWorldFinding(
                    title="Performance or control issue",
                    detail=evidence.excerpt,
                    condition="during normal operation",
                    frequency="field report",
                    severity=ConflictLevel.MAJOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["overheat", "thermal", "过热", "温度"]):
                finding = RealWorldFinding(
                    title="Thermal or overheating concern",
                    detail=evidence.excerpt,
                    condition="under load or extended use",
                    frequency="field report",
                    severity=ConflictLevel.MAJOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["battery", "续航", "standby drain", "耗电"]):
                finding = RealWorldFinding(
                    title="Battery or endurance concern",
                    detail=evidence.excerpt,
                    condition="daily use",
                    frequency="field report",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["heavy", "压手", "太重", "front-heavy", "bulky"]):
                finding = RealWorldFinding(
                    title="Weight or ergonomics concern",
                    detail=evidence.excerpt,
                    condition="during extended use",
                    frequency="field report",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["noise", "rattle", "buzz", "异响", "噪音"]):
                finding = RealWorldFinding(
                    title="Noise or rattle issue",
                    detail=evidence.excerpt,
                    condition="during operation",
                    frequency="field report",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["disappoint", "regret", "avoid", "劝退", "翻车", "misleading", "虚标"]):
                finding = RealWorldFinding(
                    title="User dissatisfaction report",
                    detail=evidence.excerpt,
                    condition="after purchase or extended use",
                    frequency="field report",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["purple fringing", "chromatic aberration", "aberration", "紫边", "色散"]):
                finding = RealWorldFinding(
                    title="Reported performance tradeoff",
                    detail=evidence.excerpt,
                    condition="specific usage scenario",
                    frequency="reported by field users",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
                
            if finding and finding.title not in seen_titles:
                seen_titles.add(finding.title)
                findings.append(finding)
        return findings

    def enrich_prices_with_ocr(self, sku: str, prices: list[PriceFinding]) -> list[PriceFinding]:
        return prices

    def arbitrate_conflicts(
        self,
        findings: list[RealWorldFinding],
        official_specs: list[OfficialSpec] | None = None,
    ) -> list[ConflictWarning]:
        """Arbitrate conflicts between official specs and real-world findings.
        
        NOTE: This keyword-based fallback provides generic conflict detection.
        In production with OpenAI/Gemini, the arbitration is done intelligently
        based on the actual product context and spec names.
        """
        warnings: list[ConflictWarning] = []
        spec_names = [spec.name for spec in (official_specs or [])]
        
        for finding in findings:
            # Generic arbitration: map findings to related spec fields when possible
            # Otherwise, create a general warning
            
            related_field = "parameter_a" if spec_names else "general_spec"
            if spec_names:
                related_field = spec_names[0]

            title_lower = finding.title.lower()
            if any(term in title_lower for term in ["defect", "quality", "performance", "control", "thermal", "battery"]):
                warnings.append(
                    ConflictWarning(
                        field=related_field,
                        official_claim="Official specifications may not cover this real-world behavior.",
                        real_world_claim=finding.detail,
                        level=finding.severity,
                        arbitration_summary="Official specs are not directly falsified, but field evidence flags a purchase-relevant risk.",
                        evidence=finding.evidence,
                    )
                )
            elif any(term in title_lower for term in ["weight", "ergonomics", "noise", "dissatisfaction", "tradeoff"]):
                warnings.append(
                    ConflictWarning(
                        field=related_field,
                        official_claim="Official specifications are factual but may omit experiential tradeoffs.",
                        real_world_claim=finding.detail,
                        level=ConflictLevel.MINOR,
                        arbitration_summary="Official specs are not contradicted; real-world evidence shows a tradeoff to consider.",
                        evidence=finding.evidence,
                    )
                )
            else:
                # Generic warning for any finding
                warnings.append(
                    ConflictWarning(
                        field=related_field,
                        official_claim="Official specifications may not cover this aspect.",
                        real_world_claim=finding.detail,
                        level=finding.severity,
                        arbitration_summary="Field evidence shows a consideration not covered by official specs.",
                        evidence=finding.evidence,
                    )
                )
        return warnings

    def summarize(self, warnings: list[ConflictWarning], findings: list[RealWorldFinding]) -> str:
        if any(warning.level == ConflictLevel.MAJOR for warning in warnings):
            return "Official specifications are usable, but field evidence shows a major handling or QC risk that should be considered before purchase."
        if findings:
            return "Official specifications are broadly consistent; real-world reports show minor tradeoffs worth noting."
        return "No evidence-backed real-world flaws were found in the collected corpus."


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
        payload = _parse_json_payload(response.text or "", default={"specs": [], "highlights": []})
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
        payload = _parse_json_payload(response.text or "", default={"findings": []})
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
        payload = _parse_json_payload(response.text or "", default={})
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


def _parse_json_payload(text: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
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


def create_model_router(mode: str | None = None) -> KeywordModelRouter:
    resolved = mode or settings.model_mode
    if resolved in {"hybrid", "partial", "keyword"} and (settings.has_gemini or settings.has_openai):
        return HybridModelRouter(resolved)
    return KeywordModelRouter()


ModelRouter = create_model_router
