#!/usr/bin/env python3
"""One-shot real-mode comparison for local validation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model_router import create_model_router
from backend.pipeline import SpecsFirstPipeline
from collectors.real import RealCollector

SOURCE_URLS = [
    "https://item.jd.com/100012043978.html",
    "https://detail.tmall.com/item.htm?id=520813140663",
    "https://www.bilibili.com/video/BV1GJ411x7h7",
]

QUERY = "Sony FE 50mm f1.2 GM"
CATEGORY = "Lens"
SELECTED_SKUS = ["Sony FE 50mm f/1.2 GM Lens (Sony E)"]


def main() -> int:
    router = create_model_router()
    collector = RealCollector(source_urls=SOURCE_URLS, router=router)
    pipeline = SpecsFirstPipeline(collector=collector, router=router, vault_path=Path("vault_output"))

    print("Starting real comparison...", flush=True)
    print("Query:", QUERY)
    print("Source URLs:", len(SOURCE_URLS))
    print("Selected SKU:", SELECTED_SKUS[0])

    result = pipeline.run(
        query=QUERY,
        category=CATEGORY,
        selected_skus=SELECTED_SKUS,
        source_urls=SOURCE_URLS,
        use_browser=True,
        task_id="live-comparison-20260710",
    )

    report = {
        "state": result.state.value,
        "task_id": result.task_id,
        "selected_skus": [c.sku for c in result.selected_candidates],
        "assets": [],
        "output_paths": [str(p) for p in result.output_paths],
        "diagnostics": result.diagnostics,
        "events_tail": [
            {"type": e.event_type, "message": e.message, "state": e.state.value}
            for e in result.events[-15:]
        ],
    }

    for asset in result.assets:
        report["assets"].append(
            {
                "sku": asset.sku,
                "official_specs_count": len(asset.official_specs),
                "official_spec_names": [s.name for s in asset.official_specs[:12]],
                "highlights": asset.spec_highlights[:5],
                "findings_count": len(asset.real_world_findings),
                "finding_samples": [
                    {
                        "platform": f.platform,
                        "summary": f.summary[:120],
                        "evidence_url": f.evidence[0].url if f.evidence else "",
                    }
                    for f in asset.real_world_findings[:5]
                ],
                "prices_count": len(asset.prices),
                "prices": [
                    {"platform": p.platform, "final_price": p.final_price}
                    for p in asset.prices[:5]
                ],
                "price_real_world_min": asset.price_real_world_min,
                "conflict_warnings_count": len(asset.conflict_warnings),
                "arbitration_summary": (asset.arbitration_summary or "")[:300],
            }
        )

    out = Path("vault_output/live_run_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== RESULT ===")
    print("state:", result.state.value)
    print("assets:", len(result.assets))
    for asset in result.assets:
        print(
            f"  {asset.sku}: specs={len(asset.official_specs)} "
            f"findings={len(asset.real_world_findings)} prices={len(asset.prices)}"
        )
    print("report:", out)
    print("vault files:", len(result.output_paths))
    return 0 if result.state.value == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
