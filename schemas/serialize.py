from __future__ import annotations

from typing import Any

from schemas import (
    ConflictLevel,
    ConflictWarning,
    EvidenceItem,
    OfficialSpec,
    PriceFinding,
    ProductAsset,
    ProductCandidate,
    RealWorldFinding,
)


def candidate_from_dict(data: dict[str, Any]) -> ProductCandidate:
    return ProductCandidate(
        sku=data["sku"],
        brand=data["brand"],
        category=data["category"],
        source_url=data["source_url"],
        confidence=float(data["confidence"]),
    )


def evidence_from_dict(data: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        platform=data["platform"],
        url=data["url"],
        author=data["author"],
        locator=data["locator"],
        captured_at=data["captured_at"],
        excerpt=data["excerpt"],
        confidence=float(data["confidence"]),
    )


def official_spec_from_dict(data: dict[str, Any]) -> OfficialSpec:
    return OfficialSpec(
        name=data["name"],
        value=data["value"],
        unit=data.get("unit", ""),
        source_url=data["source_url"],
    )


def finding_from_dict(data: dict[str, Any]) -> RealWorldFinding:
    return RealWorldFinding(
        title=data["title"],
        detail=data["detail"],
        condition=data["condition"],
        frequency=data["frequency"],
        severity=ConflictLevel(data["severity"]),
        evidence=[evidence_from_dict(item) for item in data["evidence"]],
    )


def price_from_dict(data: dict[str, Any]) -> PriceFinding:
    return PriceFinding(
        platform=data["platform"],
        list_price=float(data["list_price"]),
        coupon_discount=float(data["coupon_discount"]),
        subsidy_discount=float(data["subsidy_discount"]),
        cross_store_discount=float(data["cross_store_discount"]),
        final_price=float(data["final_price"]),
        screenshot_path=data.get("screenshot_path", ""),
        captured_at=data["captured_at"],
        evidence=evidence_from_dict(data["evidence"]),
    )


def warning_from_dict(data: dict[str, Any]) -> ConflictWarning:
    return ConflictWarning(
        field=data["field"],
        official_claim=data["official_claim"],
        real_world_claim=data["real_world_claim"],
        level=ConflictLevel(data["level"]),
        arbitration_summary=data["arbitration_summary"],
        evidence=[evidence_from_dict(item) for item in data["evidence"]],
    )


def asset_from_dict(data: dict[str, Any]) -> ProductAsset:
    return ProductAsset(
        sku=data["sku"],
        brand=data["brand"],
        category=data["category"],
        official_specs=[official_spec_from_dict(item) for item in data["official_specs"]],
        spec_highlights=list(data.get("spec_highlights", [])),
        real_world_findings=[finding_from_dict(item) for item in data["real_world_findings"]],
        prices=[price_from_dict(item) for item in data["prices"]],
        conflict_warnings=[warning_from_dict(item) for item in data["conflict_warnings"]],
        arbitration_summary=data["arbitration_summary"],
    )
