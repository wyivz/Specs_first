from __future__ import annotations

import csv
import re
from pathlib import Path

from schemas import ComparisonMatrix


class MatrixCsvExporter:
    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path)
        self.matrix_dir = self.vault_path / "00_Specs_First_Matrix"

    def write(self, category: str, matrix: ComparisonMatrix) -> Path:
        self.matrix_dir.mkdir(parents=True, exist_ok=True)
        path = self.matrix_dir / f"{slugify(category)}_comparison_matrix.csv"

        column_keys = [column.key for column in matrix.columns]
        with path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(column_keys)
            for row in matrix.rows:
                writer.writerow([row.get(key).value if row.get(key) else "" for key in column_keys])
        return path


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "untitled"
