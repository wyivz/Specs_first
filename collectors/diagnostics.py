from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiagnosticRecord:
    source: str
    message: str
    level: str = "warning"
    sku: str = ""


@dataclass
class CollectorDiagnostics:
    records: list[DiagnosticRecord] = field(default_factory=list)
    on_record: Callable[[DiagnosticRecord], None] | None = None

    def record(
        self,
        source: str,
        message: str,
        *,
        level: str = "warning",
        sku: str = "",
    ) -> None:
        item = DiagnosticRecord(source=source, message=message, level=level, sku=sku)
        self.records.append(item)
        if self.on_record is not None:
            try:
                self.on_record(item)
            except Exception:
                pass

    def extend(self, other: DiagnosticRecord | list[DiagnosticRecord]) -> None:
        if isinstance(other, DiagnosticRecord):
            self.record(other.source, other.message, level=other.level, sku=other.sku)
        else:
            for item in other:
                self.record(item.source, item.message, level=item.level, sku=item.sku)

    def merge(self, other: CollectorDiagnostics | None) -> None:
        if other:
            for item in other.records:
                self.record(item.source, item.message, level=item.level, sku=item.sku)

    def for_sku(self, sku: str) -> list[DiagnosticRecord]:
        return [record for record in self.records if not record.sku or record.sku == sku]

    def to_dicts(self) -> list[dict]:
        return [
            {
                "source": record.source,
                "message": record.message,
                "level": record.level,
                "sku": record.sku,
            }
            for record in self.records
        ]

    def has_errors(self) -> bool:
        return any(record.level == "error" for record in self.records)
