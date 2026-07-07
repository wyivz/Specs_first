from __future__ import annotations

from schemas.models import (
    CellStatus,
    ColumnDefinition,
    ComparisonCell,
    ComparisonMatrix,
    ConflictLevel,
    ProductAsset,
)


# Default columns for any product category - dynamically derived from official_specs
# The spec column names are determined by the product category at runtime,
# not hardcoded to any specific product type like lenses.
DEFAULT_BASE_COLUMNS = [
    ColumnDefinition("sku", "SKU"),
    ColumnDefinition("brand", "Brand"),
]

DEFAULT_TRAILER_COLUMNS = [
    ColumnDefinition("price_real_world_min", "Real-World Min Price"),
    ColumnDefinition("critical_flaws", "Critical Flaws"),
    ColumnDefinition("arbitration_summary", "Arbitration"),
]


def build_comparison_matrix(assets: list[ProductAsset]) -> ComparisonMatrix:
    """Build a comparison matrix from product assets.
    
    The spec columns are dynamically derived from the official_specs in each asset,
    making this function work for any product category (lenses, phones, keyboards, etc.).
    """
    if not assets:
        return ComparisonMatrix(columns=DEFAULT_BASE_COLUMNS + DEFAULT_TRAILER_COLUMNS, rows=[])
    
    # Collect all unique spec names across all assets (preserving order)
    all_spec_names: list[str] = []
    seen_spec_names: set[str] = set()
    for asset in assets:
        for spec in asset.official_specs:
            if spec.name not in seen_spec_names:
                all_spec_names.append(spec.name)
                seen_spec_names.add(spec.name)
    
    # Build column definitions
    spec_columns = [ColumnDefinition(name, name.replace("_", " ").title()) for name in all_spec_names]
    columns = DEFAULT_BASE_COLUMNS + spec_columns + DEFAULT_TRAILER_COLUMNS
    
    rows: list[dict[str, ComparisonCell]] = []

    for asset in assets:
        specs = {spec.name: spec for spec in asset.official_specs}
        conflict_by_field = {
            warning.field: warning for warning in asset.conflict_warnings
            if warning.level != ConflictLevel.NONE
        }
        row: dict[str, ComparisonCell] = {
            "sku": ComparisonCell(asset.sku, CellStatus.NORMAL),
            "brand": ComparisonCell(asset.brand, CellStatus.NORMAL),
        }

        # Dynamically handle all spec columns
        for key in all_spec_names:
            spec = specs.get(key)
            warning = conflict_by_field.get(key)
            if warning:
                row[key] = ComparisonCell(
                    spec.value if spec else "",
                    CellStatus.CONFLICT if warning.level == ConflictLevel.MAJOR else CellStatus.WARNING,
                    warning.evidence,
                    warning,
                )
            elif spec:
                row[key] = ComparisonCell(spec.value, CellStatus.NORMAL)
            else:
                row[key] = ComparisonCell("", CellStatus.MISSING)

        price_evidence = [price.evidence for price in asset.prices]
        row["price_real_world_min"] = ComparisonCell(
            asset.price_real_world_min,
            CellStatus.NORMAL if asset.price_real_world_min is not None else CellStatus.MISSING,
            price_evidence,
        )

        finding_evidence = [
            evidence
            for finding in asset.real_world_findings
            for evidence in finding.evidence
        ]
        row["critical_flaws"] = ComparisonCell(
            "; ".join(asset.critical_flaws),
            CellStatus.WARNING if asset.critical_flaws else CellStatus.NORMAL,
            finding_evidence,
        )

        worst_conflict = max(
            (warning.level for warning in asset.conflict_warnings),
            default=ConflictLevel.NONE,
            key=lambda level: [ConflictLevel.NONE, ConflictLevel.MINOR, ConflictLevel.MAJOR].index(level),
        )
        row["arbitration_summary"] = ComparisonCell(
            asset.arbitration_summary,
            CellStatus.CONFLICT if worst_conflict == ConflictLevel.MAJOR else CellStatus.WARNING,
            [evidence for warning in asset.conflict_warnings for evidence in warning.evidence],
        )
        rows.append(row)

    return ComparisonMatrix(columns=columns, rows=rows)


def build_partial_row(asset: ProductAsset) -> dict:
    row = build_comparison_matrix([asset]).rows[0]
    return {
        key: {
            "value": cell.value,
            "status": cell.status.value,
            "evidence": [
                {
                    "platform": evidence.platform,
                    "url": evidence.url,
                    "author": evidence.author,
                    "excerpt": evidence.excerpt,
                }
                for evidence in cell.evidence
            ],
            "warning": (
                {
                    "field": cell.warning.field,
                    "arbitration_summary": cell.warning.arbitration_summary,
                    "level": cell.warning.level.value,
                }
                if cell.warning
                else None
            ),
        }
        for key, cell in row.items()
    }
