from __future__ import annotations

from collectors.extractors import extract_specs_from_text
from schemas import ConflictLevel, ConflictWarning, EvidenceItem, OfficialSpec, PriceFinding, RealWorldFinding

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

    def extract_official_specs_from_images(
        self,
        sku: str,
        image_urls: list[str],
        source_url: str,
        category: str = "",
    ) -> tuple[list[OfficialSpec], list[str]]:
        return [], []

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
