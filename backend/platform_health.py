from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.gemini_health import RECOMMENDED_GEMINI_MODEL, build_gemini_health, resolve_gemini_model
from collectors.credentials import load_bilibili_credentials, load_jd_credentials, load_taobao_credentials

__all__ = [
    "CheckResult",
    "PlatformHealthReport",
    "build_platform_health",
    "check_gemini_model",
    "resolve_gemini_model",
    "write_health_report",
]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # ok | warn | error | skip
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformHealthReport:
    checked_at: str
    overall: str  # ok | degraded | error
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "overall": self.overall,
            "checks": [asdict(item) for item in self.checks],
        }


def check_gemini_model(*, probe_api: bool = False) -> CheckResult:
    gemini = build_gemini_health(live_probe=probe_api)
    configured = settings.gemini_model.strip()
    effective = resolve_gemini_model(configured)

    if not gemini.api_key_configured:
        return CheckResult(
            name="gemini_model",
            status="skip",
            message="GEMINI_API_KEY not set",
            details={"configured_model": configured or None, "effective_model": effective},
        )

    if gemini.model_retired:
        return CheckResult(
            name="gemini_model",
            status="warn",
            message=gemini.message,
            details={
                "configured_model": configured,
                "effective_model": effective,
                "recommended_model": RECOMMENDED_GEMINI_MODEL,
            },
        )

    if probe_api and gemini.live_probe_ok is False:
        return CheckResult(
            name="gemini_model",
            status="error",
            message=gemini.message or f"Gemini probe failed for {effective!r}",
            details={"configured_model": configured, "effective_model": effective},
        )

    if probe_api and gemini.live_probe_ok is True:
        return CheckResult(
            name="gemini_model",
            status="ok",
            message=f"Gemini probe succeeded with {effective!r}",
            details={"configured_model": configured, "effective_model": effective},
        )

    return CheckResult(
        name="gemini_model",
        status="ok",
        message=f"Gemini model {effective!r} configured",
        details={"configured_model": configured, "effective_model": effective},
    )


def check_openai_key() -> CheckResult:
    if settings.has_openai:
        return CheckResult(
            name="openai_key",
            status="ok",
            message=f"OpenAI configured ({settings.openai_model})",
            details={"model": settings.openai_model},
        )
    return CheckResult(
        name="openai_key",
        status="warn",
        message="OPENAI_API_KEY not set; Phase 4 arbitration falls back to keyword rules",
    )


def check_bilibili_credentials() -> CheckResult:
    creds = load_bilibili_credentials()
    if creds.configured:
        return CheckResult(
            name="bilibili_credentials",
            status="ok",
            message="Bilibili session cookies configured",
            details={"has_buvid3": bool(creds.buvid3)},
        )
    return CheckResult(
        name="bilibili_credentials",
        status="warn",
        message="Bilibili cookies missing; subtitles/comments API will be HTML-only",
        details={"required": ["BILIBILI_SESSDATA", "BILIBILI_BILI_JCT", "BILIBILI_DEDEUSERID"]},
    )


def check_taobao_credentials() -> CheckResult:
    creds = load_taobao_credentials()
    if creds.configured:
        return CheckResult(
            name="taobao_credentials",
            status="ok",
            message="Taobao/Tmall mtop signing token configured",
            details={"has_full_cookie": bool(creds.cookie_header())},
        )
    return CheckResult(
        name="taobao_credentials",
        status="warn",
        message="Taobao/Tmall cookies missing; mtop APIs and prices may fail",
        details={"required": ["TAOBAO_COOKIE or TAOBAO_M_H5_TK"]},
    )


def check_jd_credentials() -> CheckResult:
    creds = load_jd_credentials()
    if creds.configured:
        return CheckResult(
            name="jd_credentials",
            status="ok",
            message="JD session cookies configured",
            details={"recommended": ["pt_key", "pt_pin"]},
        )
    return CheckResult(
        name="jd_credentials",
        status="warn",
        message="JD cookies missing; prices may be unavailable outside browser-only HTML",
        details={"required": ["JD_COOKIE"]},
    )


def check_collector_mode() -> CheckResult:
    mode = settings.default_mode.strip().lower() or "mock"
    if mode == "real":
        return CheckResult(
            name="collector_mode",
            status="ok",
            message="SPECS_FIRST_MODE=real",
            details={"mode": mode},
        )
    return CheckResult(
        name="collector_mode",
        status="skip",
        message=f"SPECS_FIRST_MODE={mode} (mock does not hit live platforms)",
        details={"mode": mode},
    )


def _overall_status(checks: list[CheckResult]) -> str:
    if any(item.status == "error" for item in checks):
        return "error"
    if any(item.status == "warn" for item in checks):
        return "degraded"
    return "ok"


def build_platform_health(*, probe_gemini: bool = False) -> PlatformHealthReport:
    checks = [
        check_gemini_model(probe_api=probe_gemini),
        check_openai_key(),
        check_bilibili_credentials(),
        check_taobao_credentials(),
        check_jd_credentials(),
        check_collector_mode(),
    ]
    return PlatformHealthReport(
        checked_at=datetime.now(UTC).isoformat(),
        overall=_overall_status(checks),
        checks=checks,
    )


def write_health_report(report: PlatformHealthReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
