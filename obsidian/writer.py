from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from schemas import ComparisonMatrix, ProductAsset


class ObsidianWriter:
    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path)
        self.matrix_dir = self.vault_path / "00_Specs_First_Matrix"
        self.items_dir = self.vault_path / "01_Product_Items"

    def write(self, category: str, assets: list[ProductAsset], matrix: ComparisonMatrix) -> list[Path]:
        self.matrix_dir.mkdir(parents=True, exist_ok=True)
        self.items_dir.mkdir(parents=True, exist_ok=True)
        paths = [self.write_asset(asset) for asset in assets]
        paths.append(self.write_matrix_view(category, matrix))
        return paths

    def write_asset(self, asset: ProductAsset) -> Path:
        path = self.items_dir / f"{slugify(asset.sku)}.md"
        frontmatter = {
            "tags": ["Specs-First", f"Product/{asset.category}"],
            "sku": asset.sku,
            "brand": asset.brand,
            "price_real_world_min": asset.price_real_world_min,
            "spec_highlights": asset.spec_highlights,
            "critical_flaws": asset.critical_flaws,
            "arbitration_summary": asset.arbitration_summary,
        }
        for spec in asset.official_specs:
            frontmatter[f"{spec.name}_official"] = spec.value

        lines = ["---", *yaml_lines(frontmatter), "---", ""]
        lines.extend([
            f"# 🔎 Specs-First 脱水报告: {asset.sku}",
            "",
            "## 🎯 核心仲裁结论",
            "> [!WARNING]",
            f"> **冲突仲裁结果**：{asset.arbitration_summary}",
            "",
            "## 📊 官方规格",
        ])
        for spec in asset.official_specs:
            lines.append(f"- {spec.name}: {spec.value} ([官方来源]({spec.source_url}))")

        lines.extend(["", "## 💥 民间实测翻车点"])
        for finding in asset.real_world_findings:
            lines.append(f"- {finding.title}: {finding.detail}")
            for evidence in finding.evidence:
                lines.append(f"  - {evidence.platform} / {evidence.author} / {evidence.locator}: [{evidence.excerpt}]({evidence.url})")

        lines.extend(["", "## 💰 价格证据"])
        for price in asset.prices:
            lines.append(
                f"- {price.platform}: list {price.list_price}, coupon -{price.coupon_discount}, "
                f"subsidy -{price.subsidy_discount}, cross-store -{price.cross_store_discount}, "
                f"final {price.final_price} ([source]({price.evidence.url}))"
            )

        lines.extend(["", "## ⚖️ 冲突警告"])
        for warning in asset.conflict_warnings:
            lines.append(f"- {warning.level}: {warning.field} - {warning.arbitration_summary}")
            for evidence in warning.evidence:
                lines.append(f"  - [{evidence.excerpt}]({evidence.url})")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_matrix_view(self, category: str, matrix: ComparisonMatrix) -> Path:
        path = self.matrix_dir / f"{slugify(category)}_progressive_comparison_matrix.md"
        content = "\n".join([
            f"# 📷 {category} 参数与真实翻车点横向比对矩阵",
            "",
            "```dataview",
            "TABLE",
            '    focal_length_official AS "焦距"',
            '    max_aperture_official AS "最大光圈"',
            '    weight_official AS "重量"',
            '    optical_structure_official AS "官方结构"',
            '    spec_highlights AS "独有特性"',
            '    price_real_world_min AS "真实到手价(元)"',
            '    critical_flaws AS "💥 民间实测翻车点"',
            '    arbitration_summary AS "⚖️ 终审仲裁"',
            'FROM #Specs-First AND "01_Product_Items"',
            "SORT price_real_world_min ASC",
            "```",
            "",
            "## 前端矩阵快照",
            "",
            "| SKU | Price | Flaws | Arbitration |",
            "| --- | ---: | --- | --- |",
            *[
                f"| {row['sku'].value} | {row['price_real_world_min'].value} | {row['critical_flaws'].value} | {row['arbitration_summary'].value} |"
                for row in matrix.rows
            ],
        ])
        path.write_text(content + "\n", encoding="utf-8")
        return path


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "untitled"


def yaml_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in data.items():
        lines.extend(format_yaml_value(key, value))
    return lines


def format_yaml_value(key: str, value: Any) -> list[str]:
    if value is None:
        return [f"{key}: null"]
    if isinstance(value, str):
        return [f'{key}: "{escape_yaml_string(value)}"']
    if isinstance(value, (int, float)):
        return [f"{key}: {value}"]
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        lines = [f"{key}:"]
        for item in value:
            lines.append(f'  - "{escape_yaml_string(str(item))}"')
        return lines
    return [f'{key}: "{escape_yaml_string(str(value))}"']


def escape_yaml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
