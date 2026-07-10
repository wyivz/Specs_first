from __future__ import annotations

import re

# Well-known non-product / honeypot / meme BV ids — not useful for SKU evidence.
BLOCKED_BVIDS = frozenset(
    {
        "BV1GJ411x7h7",  # 镇站之宝 Rick Roll — common placeholder in old smoke tests
    }
)

RICKROLL_TITLE_HINTS = re.compile(
    r"never\s+gonna\s+give\s+you\s+up|rick\s*roll|镇站之宝",
    re.I,
)


def is_blocked_bvid(bvid: str) -> bool:
    return bvid.upper() in {item.upper() for item in BLOCKED_BVIDS}


def is_rickroll_title(title: str) -> bool:
    return bool(RICKROLL_TITLE_HINTS.search(title or ""))
