from __future__ import annotations

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

    def record(
        self,
        source: str,
        message: str,
        *,
        level: str = "warning",
        sku: str = "",
    ) -> None:
        self.records.append(DiagnosticRecord(source=source, message=message, level=level, sku=sku))

    def extend(self, other: DiagnosticRecord | list[DiagnosticRecord]) -> None:
        if isinstance(other, DiagnosticRecord):
            self.records.append(other)
        else:
            self.records.extend(other)

    def merge(self, other: CollectorDiagnostics | None) -> None:
        if other:
            self.records.extend(other.records)

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
