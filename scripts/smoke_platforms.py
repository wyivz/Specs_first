#!/usr/bin/env python3
"""Minimal live probes for platform availability (P0 smoke checks).

Usage:
  python scripts/smoke_platforms.py
  python scripts/smoke_platforms.py --probe-gemini --output vault_output/smoke_report.json

Exit codes:
  0 — all executed probes passed (skipped checks do not fail the run)
  1 — at least one probe failed
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.platform_health import build_platform_health, write_health_report  # noqa: E402
from collectors.adapters.bilibili_api_client import BilibiliApiClient  # noqa: E402
from collectors.adapters.jd import JdAdapter  # noqa: E402
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter  # noqa: E402
from collectors.adapters.youtube import YouTubeAdapter  # noqa: E402
from collectors.credentials import load_bilibili_credentials, load_taobao_credentials  # noqa: E402
from collectors.http import HttpClient  # noqa: E402
from collectors.platform_auth import PlatformAuthRequired  # noqa: E402

# Stable public pages used only for connectivity / parsing smoke tests.
_SMOKE_JD_URL = "https://item.jd.com/100012043978.html"
_SMOKE_TAOBAO_ITEM_ID = "520813140663"
_SMOKE_BILIBILI_BVID = "BV1GJ411x7h7"
_SMOKE_YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_SMOKE_DDG_QUERY = "site:jd.com 手机"


@dataclass
class SmokeProbe:
    name: str
    status: str  # pass | fail | skip
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmokeReport:
    checked_at: str
    overall: str  # pass | fail
    config: dict[str, Any] = field(default_factory=dict)
    probes: list[SmokeProbe] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "overall": self.overall,
            "config": self.config,
            "probes": [asdict(item) for item in self.probes],
        }


def _probe_http_fetch(name: str, url: str, *, min_chars: int = 200) -> SmokeProbe:
    http = HttpClient(timeout_seconds=15, retries=1)
    result = http.fetch(url)
    if not result.ok:
        return SmokeProbe(
            name=name,
            status="fail",
            message=f"HTTP fetch failed: {result.error or result.status}",
            details={"url": url, "status": result.status},
        )
    if len(result.text) < min_chars:
        return SmokeProbe(
            name=name,
            status="fail",
            message=f"Response too short ({len(result.text)} chars)",
            details={"url": url, "status": result.status},
        )
    return SmokeProbe(
        name=name,
        status="pass",
        message=f"Fetched {len(result.text)} chars",
        details={"url": url, "status": result.status},
    )


def probe_jd_page() -> SmokeProbe:
    adapter = JdAdapter()
    url = adapter.normalize_url(_SMOKE_JD_URL)
    probe = _probe_http_fetch("jd_page", url, min_chars=500)
    if probe.status != "pass":
        return probe
    http = HttpClient(timeout_seconds=15, retries=1)
    markup = http.fetch(url).text
    has_title = "jd.com" in markup.lower() or len(markup) > 1000
    sku = adapter._extract_sku_id(url, markup)  # noqa: SLF001 — smoke script
    if not has_title:
        return SmokeProbe("jd_page", "fail", "JD page markup looks empty or blocked", {"url": url})
    return SmokeProbe(
        "jd_page",
        "pass",
        f"JD product page reachable (sku={sku or 'unknown'})",
        {"url": url, "sku": sku},
    )


def probe_taobao_mtop() -> SmokeProbe:
    creds = load_taobao_credentials()
    if not creds.configured:
        return SmokeProbe(
            "taobao_mtop",
            "skip",
            "Taobao cookies not configured",
            {"item_id": _SMOKE_TAOBAO_ITEM_ID},
        )
    adapter = TmallTaobaoAdapter(credentials=creds)
    product_url = f"https://detail.tmall.com/item.htm?id={_SMOKE_TAOBAO_ITEM_ID}"
    signed_url = adapter.build_signed_mtop_url(
        "mtop.taobao.detail.getdesc",
        "6.0",
        {"id": _SMOKE_TAOBAO_ITEM_ID},
        host="h5api.m.tmall.com",
    )
    http = HttpClient(timeout_seconds=15, retries=1)
    try:
        payload = adapter.fetch_mtop_payload(http, signed_url, referer=product_url)
    except PlatformAuthRequired as exc:
        return SmokeProbe(
            "taobao_mtop",
            "pass",
            "mtop reachable but needs captcha/login (PlatformAuthRequired)",
            {"url": signed_url, "auth": str(exc)},
        )
    except Exception as exc:
        return SmokeProbe("taobao_mtop", "fail", f"mtop request failed: {exc}", {"url": signed_url})

    parsed = adapter.parse_mtop_json(payload)
    if not parsed:
        return SmokeProbe("taobao_mtop", "fail", "mtop response is not valid JSON", {"preview": payload[:200]})
    ret = parsed.get("ret", [])
    ret_text = " ".join(ret) if isinstance(ret, list) else str(ret)
    if "SUCCESS" in ret_text:
        return SmokeProbe("taobao_mtop", "pass", "mtop getdesc SUCCESS", {"ret": ret_text})
    if any(marker in ret_text for marker in ("TOKEN", "SESSION", "RGV587", "LOGIN")):
        return SmokeProbe(
            "taobao_mtop",
            "pass",
            "mtop reachable but session/captcha required",
            {"ret": ret_text},
        )
    return SmokeProbe("taobao_mtop", "fail", f"unexpected mtop ret: {ret_text}", {"ret": ret_text})


def probe_bilibili_api() -> SmokeProbe:
    creds = load_bilibili_credentials()
    if not creds.configured:
        return SmokeProbe("bilibili_api", "skip", "Bilibili cookies not configured", {"bvid": _SMOKE_BILIBILI_BVID})
    client = BilibiliApiClient(credentials=creds)
    try:
        subtitle = client.fetch_subtitle_text(_SMOKE_BILIBILI_BVID)
    except PlatformAuthRequired as exc:
        return SmokeProbe(
            "bilibili_api",
            "pass",
            "Bilibili API reachable but verification required",
            {"bvid": _SMOKE_BILIBILI_BVID, "auth": str(exc)},
        )
    except Exception as exc:
        return SmokeProbe("bilibili_api", "fail", f"Bilibili API failed: {exc}", {"bvid": _SMOKE_BILIBILI_BVID})

    if subtitle.strip():
        return SmokeProbe(
            "bilibili_api",
            "pass",
            f"subtitle fetched ({len(subtitle)} chars)",
            {"bvid": _SMOKE_BILIBILI_BVID},
        )
    return SmokeProbe(
        "bilibili_api",
        "pass",
        "Bilibili API reachable (no CC subtitle on probe video; ASR may apply)",
        {"bvid": _SMOKE_BILIBILI_BVID},
    )


def probe_youtube_transcript() -> SmokeProbe:
    http = HttpClient(timeout_seconds=15, retries=1)
    adapter = YouTubeAdapter(http=http)
    page = http.fetch(_SMOKE_YOUTUBE_URL)
    if not page.ok:
        return SmokeProbe(
            "youtube_transcript",
            "fail",
            f"YouTube watch page fetch failed: {page.error or page.status}",
            {"url": _SMOKE_YOUTUBE_URL},
        )
    video_id = adapter.extract_video_id(_SMOKE_YOUTUBE_URL)
    transcript = adapter.fetch_transcript(_SMOKE_YOUTUBE_URL, markup=page.text, video_id=video_id)
    html_comments = adapter._extract_comment_snippets(page.text)  # noqa: SLF001
    if transcript.strip():
        return SmokeProbe(
            "youtube_transcript",
            "pass",
            f"transcript available ({len(transcript)} chars)",
            {"video_id": video_id},
        )
    if html_comments:
        return SmokeProbe(
            "youtube_transcript",
            "pass",
            "transcript unavailable (PoToken likely); HTML comments present",
            {"video_id": video_id, "html_comment_snippets": len(html_comments)},
        )
    return SmokeProbe(
        "youtube_transcript",
        "fail",
        "no transcript and no HTML comment snippets (PoToken / IP block likely)",
        {"video_id": video_id},
    )


def probe_duckduckgo_search() -> SmokeProbe:
    http = HttpClient(timeout_seconds=15, retries=1)
    results = http.search(_SMOKE_DDG_QUERY, max_results=3)
    if not results:
        return SmokeProbe(
            "duckduckgo_search",
            "fail",
            "DuckDuckGo returned no results (CAPTCHA / rate limit likely)",
            {"query": _SMOKE_DDG_QUERY},
        )
    return SmokeProbe(
        "duckduckgo_search",
        "pass",
        f"{len(results)} search result(s)",
        {"query": _SMOKE_DDG_QUERY, "first_url": results[0].url},
    )


def run_smoke(*, probe_gemini: bool = False) -> SmokeReport:
    config_report = build_platform_health(probe_gemini=probe_gemini)
    probes: list[SmokeProbe] = [
        probe_jd_page(),
        probe_taobao_mtop(),
        probe_bilibili_api(),
        probe_youtube_transcript(),
        probe_duckduckgo_search(),
    ]
    for check in config_report.checks:
        if check.status == "error":
            probes.append(
                SmokeProbe(
                    name=f"config_{check.name}",
                    status="fail",
                    message=check.message,
                    details=check.details,
                )
            )
        elif check.status == "warn" and check.name == "gemini_model":
            probes.append(
                SmokeProbe(
                    name=f"config_{check.name}",
                    status="pass",
                    message=check.message,
                    details=check.details,
                )
            )

    overall = "pass" if all(item.status in {"pass", "skip"} for item in probes) else "fail"
    return SmokeReport(
        checked_at=datetime.now(UTC).isoformat(),
        overall=overall,
        config=config_report.to_dict(),
        probes=probes,
    )


def _print_report(report: SmokeReport) -> None:
    print(f"Specs-First platform smoke — overall: {report.overall}")
    print(f"checked_at: {report.checked_at}")
    print()
    print("Config:")
    for check in report.config.get("checks", []):
        print(f"  [{check['status'].upper():5}] {check['name']}: {check['message']}")
    print()
    print("Live probes:")
    for probe in report.probes:
        print(f"  [{probe.status.upper():4}] {probe.name}: {probe.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Specs-First platform smoke probes")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("vault_output/smoke_report.json"),
        help="Write JSON report to this path",
    )
    parser.add_argument(
        "--probe-gemini",
        action="store_true",
        help="Call Gemini API once to verify the configured model",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Only run configuration health checks (no live platform HTTP)",
    )
    args = parser.parse_args(argv)

    if args.health_only:
        health = build_platform_health(probe_gemini=args.probe_gemini)
        write_health_report(health, args.output)
        print(json.dumps(health.to_dict(), ensure_ascii=False, indent=2))
        return 0 if health.overall != "error" else 1

    report = run_smoke(probe_gemini=args.probe_gemini)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(report)
    print()
    print(f"Report written to {args.output}")
    return 0 if report.overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
