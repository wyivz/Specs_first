from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.config import settings
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
    """Deterministic fallback when API keys are absent."""

    def extract_official_specs_from_text(
        self,
        sku: str,
        text: str,
        source_url: str,
    ) -> tuple[list[OfficialSpec], list[str]]:
        from collectors.extractors import extract_specs_from_text

        return extract_specs_from_text(text, source_url), []

    def extract_real_world_findings(self, sku: str, corpus: list[EvidenceItem]) -> list[RealWorldFinding]:
        findings: list[RealWorldFinding] = []
        seen_titles: set[str] = set()
        for evidence in corpus:
            text = evidence.excerpt.lower()
            finding: RealWorldFinding | None = None
            if any(term in text for term in ["purple fringing", "chromatic aberration", "longitudinal ca", "紫边", "色散"]):
                finding = RealWorldFinding(
                    title="Visible chromatic aberration",
                    detail=evidence.excerpt,
                    condition="high-contrast or wide-open shooting",
                    frequency="reported by field users",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["focus ring", "damping", "sticky", "对焦环", "阻尼", "卡顿"]):
                finding = RealWorldFinding(
                    title="Uneven focus ring damping",
                    detail=evidence.excerpt,
                    condition="manual focusing near close-focus range",
                    frequency="copy-variation report",
                    severity=ConflictLevel.MAJOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["front-heavy", "heavy", "压手", "太重", "重量"]):
                finding = RealWorldFinding(
                    title="Front-heavy handling",
                    detail=evidence.excerpt,
                    condition="small camera bodies",
                    frequency="single field report",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["flare", "ghosting", "眩光", "鬼影"]):
                finding = RealWorldFinding(
                    title="Flare or ghosting risk",
                    detail=evidence.excerpt,
                    condition="strong backlight or point light sources",
                    frequency="field report",
                    severity=ConflictLevel.MINOR,
                    evidence=[evidence],
                )
            elif any(term in text for term in ["copy variation", "decenter", "品控", "偏心"]):
                finding = RealWorldFinding(
                    title="Copy variation or QC risk",
                    detail=evidence.excerpt,
                    condition="sample-dependent",
                    frequency="field report",
                    severity=ConflictLevel.MAJOR,
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
        warnings: list[ConflictWarning] = []
        for finding in findings:
            if finding.title == "Uneven focus ring damping":
                warnings.append(
                    ConflictWarning(
                        field="minimum_focus_distance",
                        official_claim="Official close-focus capability is valid, but ergonomics are not specified.",
                        real_world_claim=finding.detail,
                        level=ConflictLevel.MAJOR,
                        arbitration_summary="Official close-focus specs remain valid; manual focus feel has credible copy-variation risk.",
                        evidence=finding.evidence,
                    )
                )
            elif finding.title == "Visible chromatic aberration":
                warnings.append(
                    ConflictWarning(
                        field="optical_structure",
                        official_claim="Official optical structure is a factual specification.",
                        real_world_claim=finding.detail,
                        level=ConflictLevel.MINOR,
                        arbitration_summary="Official optical formula is not contradicted; real-world edge CA is a meaningful optical tradeoff.",
                        evidence=finding.evidence,
                    )
                )
            elif finding.title in {"Copy variation or QC risk", "Flare or ghosting risk", "Front-heavy handling"}:
                warnings.append(
                    ConflictWarning(
                        field="optical_structure",
                        official_claim="Official optical specifications do not cover sample variation or scene-dependent artifacts.",
                        real_world_claim=finding.detail,
                        level=finding.severity,
                        arbitration_summary="The official specs are not directly falsified, but field evidence flags a purchase-relevant risk.",
                        evidence=finding.evidence,
                    )
                )
        return warnings

    def summarize(self, warnings: list[ConflictWarning], findings: list[RealWorldFinding]) -> str:
        if any(warning.level == ConflictLevel.MAJOR for warning in warnings):
            return "Official specifications are usable, but field evidence shows a major handling or QC risk that should be considered before purchase."
        if findings:
            return "Official specifications are broadly consistent; real-world reports show minor optical or handling tradeoffs."
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
    ) -> tuple[list[OfficialSpec], list[str]]:
        if settings.has_gemini and text.strip():
            try:
                specs, highlights = self._gemini_extract_official_specs(sku, text, source_url)
                if specs:
                    return specs, highlights
            except Exception:
                pass
        return self._keyword.extract_official_specs_from_text(sku, text, source_url)

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
            if not price.screenshot_path:
                enriched.append(price)
                continue
            screenshot = Path(price.screenshot_path)
            if not screenshot.exists():
                enriched.append(price)
                continue
            try:
                parsed = self._gemini_ocr_price(sku, screenshot, price.evidence.url)
            except Exception:
                enriched.append(price)
                continue
            if not parsed:
                enriched.append(price)
                continue
            enriched.append(
                PriceFinding(
                    platform=price.platform,
                    list_price=parsed.list_price,
                    coupon_discount=parsed.coupon_discount,
                    subsidy_discount=parsed.subsidy_discount,
                    cross_store_discount=parsed.cross_store_discount,
                    final_price=parsed.final_price,
                    screenshot_path=price.screenshot_path,
                    captured_at=price.captured_at,
                    evidence=price.evidence,
                )
            )
        return enriched or prices

    def arbitrate_conflicts(
        self,
        findings: list[RealWorldFinding],
        official_specs: list[OfficialSpec] | None = None,
    ) -> list[ConflictWarning]:
        self._last_summary = ""
        if settings.has_openai and findings:
            try:
                warnings = self._openai_arbitrate_structured(findings, official_specs or [])
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

    def _gemini_extract_official_specs(
        self,
        sku: str,
        text: str,
        source_url: str,
    ) -> tuple[list[OfficialSpec], list[str]]:
        prompt = (
            f"Extract official product specifications for {sku} from the source text below. "
            "Return JSON only: "
            '{"specs":[{"name":"focal_length|max_aperture|weight|optical_structure|minimum_focus_distance|filter_thread","value":"","unit":""}],'
            '"highlights":["unique feature"]}. '
            "Use only factual values present in the text.\n\n"
            f"{text[:120000]}"
        )
        response = self._gemini_model().generate_content(prompt)
        payload = _parse_json_payload(response.text or "")
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
        prompt = (
            f"You are a harsh product QA reviewer for {sku}. "
            "Discard marketing fluff and subjective praise. "
            "Extract only evidence-backed real-world flaws from the corpus below. "
            "Return JSON only with shape "
            '{"findings":[{"title":"","detail":"","condition":"","frequency":"","severity":"minor|major","evidence_index":0}]}. '
            "Each finding must reference one evidence_index from the corpus.\n\n"
            f"{corpus_text}"
        )
        response = self._gemini_model().generate_content(prompt)
        payload = _parse_json_payload(response.text or "")
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
        payload = _parse_json_payload(response.text or "")
        final_price = float(payload.get("final_price", 0))
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


def _parse_json_payload(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def create_model_router(mode: str | None = None) -> KeywordModelRouter:
    resolved = mode or settings.model_mode
    if resolved in {"hybrid", "partial", "keyword"} and (settings.has_gemini or settings.has_openai):
        return HybridModelRouter(resolved)
    return KeywordModelRouter()


ModelRouter = create_model_router
