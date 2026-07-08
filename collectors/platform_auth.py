from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlatformAuthRequired(RuntimeError):
    """Raised when a platform requires manual verification (captcha, login, etc.)."""

    platform: str
    message: str
    url: str = ""
    storage_state_path: str = ""
    in_progress_payload: dict | None = None

    def __str__(self) -> str:
        return self.message


def is_verification_error(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "captcha",
        "verify",
        "validation",
        "验证",
        "滑块",
        "风控",
        "412",
        "429",
        "login",
        "登录",
    )
    return any(marker in lowered for marker in markers)
