#!/usr/bin/env python3
"""One-shot real-mode comparison for local validation.

By default this relies on automatic search (DDG + platform adapters) from the
selected SKU — same as the Streamlit/API pipeline. Optional SOURCE_URLS in .env
only add extra pinned pages; they are not required.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.model_router import create_model_router
from backend.pipeline import SpecsFirstPipeline
from collectors.real import RealCollector


def _optional_source_urls() -> list[str]:
    """Optional pinned URLs — augmentation only, not the primary discovery path."""
    raw = os.getenv("OPTIONAL_SOURCE_URLS", "").strip()
    if not raw:
        # Stable defaults for local SEL50F12GM validation when env is empty.
        return [
            "https://www.sony.com/electronics/support/lenses-e-mount-lenses/sel50f12gm/specifications",
            "https://item.jd.com/100010708487.html",
        ]
    parts: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        url = line.strip()
        if url:
            parts.append(url)
    return parts


QUERY = os.getenv("LIVE_QUERY", "Sony FE 50mm f1.2 GM")
CATEGORY = os.getenv("LIVE_CATEGORY", "Lens")
# Prefer model code — marketing titles from DDG drift and used to select 0 SKUs.
SELECTED_SKUS = [os.getenv("LIVE_SKU", "SEL50F12GM").strip()]
USE_BROWSER = os.getenv("LIVE_USE_BROWSER", "true").strip().lower() not in {"0", "false", "no"}
MODEL_MODE = os.getenv("LIVE_MODEL_MODE", "").strip() or None


def _preflight() -> None:
    missing: list[str] = []
    try:
        import google.genai  # noqa: F401
    except ImportError:
        missing.append("google-genai")
    try:
        import openai  # noqa: F401
    except ImportError:
        missing.append("openai")
    if missing:
        print(
            "WARN: missing AI packages:",
            ", ".join(missing),
            "— hybrid JIT/arbitration will degrade. Run: pip install -e .",
            flush=True,
        )


def main() -> int:
    _preflight()
    source_urls = _optional_source_urls()
    router = create_model_router(MODEL_MODE)
    collector = RealCollector(source_urls=source_urls, router=router)
    pipeline = SpecsFirstPipeline(collector=collector, router=router, vault_path=Path("vault_output"))

    print("Starting real comparison...", flush=True)
    print("Query:", QUERY, flush=True)
    print("Discovery: automatic search from SKU (Source URLs optional:", len(source_urls), ")", flush=True)
    print("Selected SKU:", SELECTED_SKUS[0] or "(auto top candidates)", flush=True)
    print("use_browser:", USE_BROWSER, flush=True)
    print("model_mode:", MODEL_MODE or "default", flush=True)

    def _on_event(event) -> None:
        print(f"[{event.state.value}] {event.event_type}: {event.message}", flush=True)

    result = pipeline.run(
        query=QUERY,
        category=CATEGORY,
        selected_skus=[sku for sku in SELECTED_SKUS if sku] or None,
        source_urls=source_urls,
        use_browser=USE_BROWSER,
        task_id=f"live-comparison-{os.getenv('LIVE_TASK_SUFFIX', 'local')}",
        on_event=_on_event,
    )

    pause_events = [
        {
            "type": e.event_type,
            "message": e.message,
            "state": e.state.value,
            "payload": e.payload,
        }
        for e in result.events
        if e.event_type in {"auth_required", "price_degraded", "sku_failed"}
    ]

    report = {
        "state": result.state.value,
        "task_id": result.task_id,
        "selected_skus": [c.sku for c in result.selected_candidates],
        "assets": [],
        "output_paths": [str(p) for p in result.output_paths],
        "diagnostics": result.diagnostics,
        "pause_or_degraded": pause_events,
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
                        "title": f.title,
                        "summary": (f.detail or f.title)[:120],
                        "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                        "evidence_platform": f.evidence[0].platform if f.evidence else "",
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
    print("\n=== RESULT ===", flush=True)
    print("state:", result.state.value, flush=True)
    print("assets:", len(result.assets), flush=True)
    for asset in result.assets:
        print(
            f"  {asset.sku}: specs={len(asset.official_specs)} "
            f"findings={len(asset.real_world_findings)} prices={len(asset.prices)}",
            flush=True,
        )
    if pause_events:
        print("pause/degraded:", flush=True)
        for item in pause_events[-5:]:
            print(" -", item.get("type"), item.get("message"), item.get("payload"), flush=True)
    print("report:", out, flush=True)
    print("vault files:", len(result.output_paths), flush=True)
    return 0 if result.state.value == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
