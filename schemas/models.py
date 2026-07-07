from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_NEED_AUTH = "PAUSED_NEED_AUTH"
    FAILED = "FAILED"
    DONE = "DONE"


class CellStatus(StrEnum):
    NORMAL = "normal"
    MISSING = "missing"
    WARNING = "warning"
    CONFLICT = "conflict"


class ConflictLevel(StrEnum):
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"


@dataclass(frozen=True)
class EvidenceItem:
    platform: str
    url: str
    author: str
    locator: str
    captured_at: str
    excerpt: str
    confidence: float

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("EvidenceItem.url is required")
        if not self.excerpt:
            raise ValueError("EvidenceItem.excerpt is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("EvidenceItem.confidence must be between 0 and 1")


@dataclass(frozen=True)
class ProductCandidate:
    sku: str
    brand: str
    category: str
    source_url: str
    confidence: float


@dataclass(frozen=True)
class OfficialSpec:
    name: str
    value: str
    unit: str
    source_url: str


@dataclass(frozen=True)
class RealWorldFinding:
    title: str
    detail: str
    condition: str
    frequency: str
    severity: ConflictLevel
    evidence: list[EvidenceItem]

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("RealWorldFinding requires at least one EvidenceItem")


@dataclass(frozen=True)
class PriceFinding:
    platform: str
    list_price: float
    coupon_discount: float
    subsidy_discount: float
    cross_store_discount: float
    final_price: float
    screenshot_path: str
    captured_at: str
    evidence: EvidenceItem


@dataclass(frozen=True)
class ConflictWarning:
    field: str
    official_claim: str
    real_world_claim: str
    level: ConflictLevel
    arbitration_summary: str
    evidence: list[EvidenceItem]

    def __post_init__(self) -> None:
        if self.level != ConflictLevel.NONE and not self.evidence:
            raise ValueError("ConflictWarning with a conflict level requires evidence")


@dataclass(frozen=True)
class ProductAsset:
    sku: str
    brand: str
    category: str
    official_specs: list[OfficialSpec]
    spec_highlights: list[str]
    real_world_findings: list[RealWorldFinding]
    prices: list[PriceFinding]
    conflict_warnings: list[ConflictWarning]
    arbitration_summary: str

    @property
    def price_real_world_min(self) -> float | None:
        if not self.prices:
            return None
        return min(price.final_price for price in self.prices)

    @property
    def critical_flaws(self) -> list[str]:
        return [finding.title for finding in self.real_world_findings]


@dataclass(frozen=True)
class ColumnDefinition:
    key: str
    label: str
    sortable: bool = True


@dataclass(frozen=True)
class ComparisonCell:
    value: Any
    status: CellStatus
    evidence: list[EvidenceItem] = field(default_factory=list)
    warning: ConflictWarning | None = None


@dataclass(frozen=True)
class ComparisonMatrix:
    columns: list[ColumnDefinition]
    rows: list[dict[str, ComparisonCell]]

    def to_plain_rows(self) -> list[dict[str, Any]]:
        return [
            {
                key: cell.value if isinstance(cell, ComparisonCell) else cell
                for key, cell in row.items()
            }
            for row in self.rows
        ]


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    event_type: str
    message: str
    state: TaskState
    payload: dict[str, Any] = field(default_factory=dict)


def to_dict(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
