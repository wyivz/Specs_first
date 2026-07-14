from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from backend.config import settings
from backend.gemini_client import get_gemini_client, resolve_gemini_model
from backend.retry import retry_call
from backend.router_keyword import KeywordModelRouter
from backend.router_schemas import ARBITRATION_SCHEMA, CATEGORY_PROFILE_SCHEMA, parse_json_payload
from collectors.extractors import ParsedPrice
from schemas import ConflictLevel, ConflictWarning, EvidenceItem, OfficialSpec, PriceFinding, RealWorldFinding
from schemas.category_profile import (
    DynamicCategoryProfile,
    canonical_slots,
    generic_category_profile,
    normalize_spec_name,
)

T = TypeVar("T")


class HybridModelRouter(KeywordModelRouter):
    """Gemini: vision survey + text/image fill. OpenAI: JIT schema + arbitration."""

    def __init__(self, mode: str | None = None) -> None:
        super().__init__()
        self.mode = mode or settings.model_mode
        self._keyword = KeywordModelRouter()
        self._last_summary = ""

    def set_category_profile(self, profile: DynamicCategoryProfile | None) -> None:
        super().set_category_profile(profile)
        self._keyword.set_category_profile(profile)

    @staticmethod
    def _run_with_timeout(fn: Callable[[], T], *, timeout_seconds: float | None = None) -> T:
        limit = settings.gemini_call_timeout_seconds if timeout_seconds is None else timeout_seconds
        if limit <= 0:
            return fn()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn)
            try:
                return future.result(timeout=limit)
            except FuturesTimeout as exc:
                future.cancel()
                raise TimeoutError(f"Gemini call exceeded {limit:.0f}s") from exc

    def survey_product_from_images(
        self,
        sku: str,
        image_urls: list[str],
        query: str = "",
        *,
        referer: str = "",
    ) -> dict[str, Any]:
        if not (settings.has_gemini and image_urls):
            return {}
        try:
            return self._run_with_timeout(
                lambda: self._gemini_survey_product_from_images(
                    sku, image_urls, query, referer=referer
                )
            )
        except Exception:
            return {}

    def build_category_profile(
        self,
        query: str,
        candidates: list | None = None,
        vision_clues: dict | None = None,
        category_hint: str = "",
    ) -> DynamicCategoryProfile:
        if settings.has_openai:
            try:
                profile = retry_call(
                    lambda: self._openai_build_category_profile(
                        query,
                        candidates or [],
                        vision_clues or {},
                        category_hint,
                    ),
                    attempts=2,
                )
                if profile is not None:
                    self.set_category_profile(profile)
                    return profile
            except Exception:
                pass
        profile = generic_category_profile(category_hint or "通用商品")
        self.set_category_profile(profile)
        return profile

    def extract_official_specs_from_text(
        self,
        sku: str,
        text: str,
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        if settings.has_gemini and text.strip():
            try:
                specs, highlights = self._run_with_timeout(
                    lambda: self._gemini_extract_official_specs(sku, text, source_url, category)
                )
                if specs:
                    return self._normalize_specs(specs, category), highlights
            except Exception:
                pass
        self._keyword.set_category_profile(self.category_profile)
        return self._keyword.extract_official_specs_from_text(sku, text, source_url, category)

    def extract_real_world_findings(self, sku: str, corpus: list[EvidenceItem]) -> list[RealWorldFinding]:
        if not corpus:
            return []
        if settings.has_gemini:
            try:
                findings = self._run_with_timeout(lambda: self._gemini_extract_findings(sku, corpus))
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
            specs, highlights = self._run_with_timeout(
                lambda: self._gemini_extract_official_specs_from_images(
                    sku, image_urls, source_url, category
                )
            )
            return self._normalize_specs(specs, category), highlights
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
        category: str = "",
    ) -> list[ConflictWarning]:
        self._last_summary = ""
        if settings.has_openai and findings:
            try:
                warnings = retry_call(
                    lambda: self._openai_arbitrate_structured(
                        findings, official_specs or [], category=category
                    ),
                    attempts=2,
                )
                if warnings is not None:
                    return warnings
            except Exception:
                pass
        self._keyword.set_category_profile(self.category_profile)
        return self._keyword.arbitrate_conflicts(findings, official_specs, category=category)

    def summarize(self, warnings: list[ConflictWarning], findings: list[RealWorldFinding]) -> str:
        if self._last_summary:
            return self._last_summary
        return self._keyword.summarize(warnings, findings)

    def _normalize_specs(
        self,
        specs: list[OfficialSpec],
        category: str,
    ) -> list[OfficialSpec]:
        profile = self.category_profile
        slots = set(canonical_slots(category, profile=profile))
        normalized: list[OfficialSpec] = []
        seen: set[str] = set()
        for spec in specs:
            name = normalize_spec_name(spec.name, category, profile=profile)
            if name in seen:
                continue
            seen.add(name)
            normalized.append(
                OfficialSpec(
                    name=name,
                    value=spec.value,
                    unit=spec.unit,
                    source_url=spec.source_url,
                )
            )
        # Keep non-slot specs; matrix will prefer slots and push extras to highlights upstream.
        del slots
        return normalized

    @contextmanager
    def _gemini_cached_content(self, corpus_text: str, system_instruction: str) -> Iterator[Any]:
        client = get_gemini_client()
        with client.cached_corpus(corpus_text, system_instruction) as cache_name:
            yield cache_name

    def _gemini_survey_product_from_images(
        self,
        sku: str,
        image_urls: list[str],
        query: str,
        *,
        referer: str = "",
    ) -> dict[str, Any]:
        from collectors.detail_images import download_detail_image

        prompt = (
            f"Survey product images for SKU '{sku}' (user query: {query or 'n/a'}). "
            "Do NOT decide final comparison columns. Return JSON only: "
            '{"likely_category":"","parameter_clues":[{"name":"","value":"","note":""}],'
            '"packaging_or_detail_notes":[""],"other_signals":[""]}. '
            "List attribute names/values visible on packaging, spec sheets, or detail graphics."
        )
        clues: dict[str, Any] = {
            "likely_category": "",
            "parameter_clues": [],
            "packaging_or_detail_notes": [],
            "other_signals": [],
        }
        for url in image_urls[:6]:
            downloaded = download_detail_image(url, referer=referer)
            if downloaded is None:
                continue
            try:
                text = get_gemini_client().generate_multimodal(
                    [prompt, {"mime_type": downloaded.mime_type, "data": downloaded.data}],
                    task="vision_json",
                )
            except Exception:
                continue
            payload = parse_json_payload(text or "", default={})
            if payload.get("likely_category") and not clues["likely_category"]:
                clues["likely_category"] = str(payload.get("likely_category", "")).strip()
            for key in ("parameter_clues", "packaging_or_detail_notes", "other_signals"):
                items = payload.get(key) or []
                if not isinstance(items, list):
                    continue
                bucket = clues[key]
                for item in items:
                    if item and item not in bucket and len(bucket) < 24:
                        bucket.append(item)
        return clues

    def _openai_build_category_profile(
        self,
        query: str,
        candidates: list,
        vision_clues: dict[str, Any],
        category_hint: str,
    ) -> DynamicCategoryProfile | None:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        candidate_rows = []
        for item in candidates[:10]:
            if hasattr(item, "sku"):
                candidate_rows.append(
                    {
                        "sku": getattr(item, "sku", ""),
                        "brand": getattr(item, "brand", ""),
                        "title_or_url": getattr(item, "source_url", ""),
                    }
                )
            elif isinstance(item, dict):
                candidate_rows.append(item)
        payload = {
            "query": query,
            "category_hint": category_hint,
            "candidates": candidate_rows,
            "vision_clues": vision_clues,
        }
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You define a universal product comparison schema for ANY category. "
                        "Pick a concise Chinese or English category_label, exactly 5-8 snake_case "
                        "hard comparison slots that buyers care about across SKUs, bilingual aliases "
                        "mapping common label substrings onto those slots, comparison_keywords for "
                        "cross-SKU matrix language, and search_modifiers for review/forum queries. "
                        "Unique SKU-only features must NOT become slots. Use OpenAI structured output only."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "category_profile",
                    "strict": True,
                    "schema": CATEGORY_PROFILE_SCHEMA,
                },
            },
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        if not parsed.get("slots"):
            return None
        profile = DynamicCategoryProfile.from_dict({**parsed, "source": "openai_jit"})
        return profile

    def _gemini_extract_official_specs(
        self,
        sku: str,
        text: str,
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        corpus = text[:120000]
        slots = canonical_slots(category, profile=self.category_profile)
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
            "invent a concise snake_case name instead of dropping it. "
            "Attributes unique to this SKU that don't belong as a comparison column go into 'highlights' instead. "
            "Include only factual values present in the text."
        )

        with self._gemini_cached_content(corpus, system_instruction) as cached_content:
            def _call() -> str:
                client = get_gemini_client()
                if cached_content:
                    return client.generate_text(
                        instruction,
                        task="corpus_extract",
                        system_instruction=system_instruction,
                        cached_content=cached_content,
                    )
                return client.generate_text(
                    f"{instruction}\n\n{corpus}",
                    task="corpus_extract",
                    system_instruction=system_instruction,
                )

            text = retry_call(_call, attempts=2)
        payload = parse_json_payload(text or "", default={"specs": [], "highlights": []})
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

        with self._gemini_cached_content(corpus_text, system_instruction) as cached_content:
            def _call() -> str:
                client = get_gemini_client()
                if cached_content:
                    return client.generate_text(
                        instruction,
                        task="corpus_extract",
                        system_instruction=system_instruction,
                        cached_content=cached_content,
                    )
                return client.generate_text(
                    f"{instruction}\n\n{corpus_text}",
                    task="corpus_extract",
                    system_instruction=system_instruction,
                )

            text = retry_call(_call, attempts=2)
        payload = parse_json_payload(text or "", default={"findings": []})
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
        text = get_gemini_client().generate_multimodal(
            [
                prompt,
                {"mime_type": "image/png", "data": screenshot.read_bytes()},
            ],
            task="vision_json",
        )
        payload = parse_json_payload(text or "", default={})
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
        from collectors.detail_images import download_detail_image

        slots = canonical_slots(category, profile=self.category_profile)
        prompt = (
            f"Extract product specifications for {sku} from these detail images. "
            "Return JSON only: "
            '{"specs":[{"name":"snake_case_parameter_name","value":"","unit":""}],"highlights":[""]}. '
            f"Prefer canonical fields: {', '.join(slots)}. Do not invent values."
        )
        specs_by_name: dict[str, OfficialSpec] = {}
        highlights: list[str] = []
        for url in image_urls[:8]:
            downloaded = download_detail_image(url, referer=source_url)
            if downloaded is None:
                continue
            try:
                text = get_gemini_client().generate_multimodal(
                    [prompt, {"mime_type": downloaded.mime_type, "data": downloaded.data}],
                    task="vision_json",
                )
            except Exception:
                continue
            payload = parse_json_payload(text or "", default={"specs": [], "highlights": []})
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
        category: str = "",
    ) -> list[ConflictWarning] | None:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        slots = list(canonical_slots(category, profile=self.category_profile))
        payload = {
            "category": category or (self.category_profile.category_label if self.category_profile else ""),
            "canonical_slots": slots,
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
                        "warning.field MUST be one of canonical_slots when possible. "
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
            field = str(item.get("field", "general"))
            if self.category_profile:
                field = normalize_spec_name(field, category, profile=self.category_profile)
            warnings.append(
                ConflictWarning(
                    field=field,
                    official_claim=str(item.get("official_claim", "")),
                    real_world_claim=str(item.get("real_world_claim", finding.detail)),
                    level=ConflictLevel(str(item.get("level", finding.severity.value))),
                    arbitration_summary=str(item.get("arbitration_summary", "")),
                    evidence=finding.evidence,
                )
            )
        self._last_summary = str(parsed.get("summary", ""))
        return warnings
