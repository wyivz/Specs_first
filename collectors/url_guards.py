"""Shared URL quality guards for search-driven collectors."""

from __future__ import annotations

from urllib.parse import urlparse

# DDG often returns these under site:jd.com / loose shopping queries.
NOISY_ECOMMERCE_HOST_HINTS = (
    "campus.jd.com",
    "music.jd.com",
    "ir.jd.com",
    "club.jd.com",
    "passport.jd.com",
    "search.jd.com",
    # JD PC frequency-control / anti-bot interstitial (reason=403).
    "pc-frequent-pro.pf.jd.com",
    "pc-frequent.pf.jd.com",
    "frequent.jd.com",
    "login.taobao.com",
    "login.tmall.com",
    "passport.taobao.com",
    "s.taobao.com",
    "list.tmall.com",
)

NOISY_ECOMMERCE_PATH_HINTS = (
    "/brand/",
    "/jiage/",
    "/hprm/",
    "/lang/",
)

# Hosts that mean "slow down / blocked", not "solve slider captcha".
RATE_LIMIT_HOST_HINTS = (
    "pc-frequent-pro.pf.jd.com",
    "pc-frequent.pf.jd.com",
    "frequent.jd.com",
)


def is_rate_limited_ecommerce_url(url: str) -> bool:
    """True for JD frequency-control interstitials (pc-frequent-pro, reason=403)."""
    if not url:
        return False
    lower = url.lower()
    if any(hint in lower for hint in RATE_LIMIT_HOST_HINTS):
        return True
    if "pf.jd.com" in lower and ("frequent" in lower or "reason=403" in lower):
        return True
    return False


def is_noisy_ecommerce_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    if "{keyword}" in url or "{" in urlparse(url).path:
        return True
    lower = url.lower()
    if is_rate_limited_ecommerce_url(url):
        return True
    if any(hint in lower for hint in NOISY_ECOMMERCE_HOST_HINTS):
        return True
    if any(hint in lower for hint in NOISY_ECOMMERCE_PATH_HINTS):
        return True
    # Marketplace homepages / brand indexes are not product sources.
    # Official collector previously accepted www.jd.com/?from=pc_item_sd and
    # harvested footer "举报电话" lines as specs.
    if _is_marketplace_non_product(url):
        return True
    return False


def is_noisy_forum_url(url: str) -> bool:
    """Skip forum indexes/homepages that look like search hits but have no thread body."""
    if not url or not url.startswith("http"):
        return True
    lower = url.lower()
    parsed = urlparse(url)
    path = (parsed.path or "/").rstrip("/") or "/"
    if "chiphell.com" in lower:
        if "forumdisplay" in lower or "mod=forumdisplay" in lower:
            return True
        if path in {"", "/"} or path.endswith("/index.php"):
            return True
        # Prefer real threads; list/search pages are low value.
        if "thread-" not in lower and "tid=" not in lower and "/thread/" not in lower:
            return True
    if "reddit.com" in lower:
        # Subreddit indexes / search pages without a post id.
        if "/comments/" not in lower:
            return True
    return False


def _is_marketplace_non_product(url: str) -> bool:
    lower = url.lower()
    if "jd.com" in lower or "jd.hk" in lower:
        from collectors.adapters.jd import JdAdapter

        return not JdAdapter().is_product_url(url)
    if "taobao.com" in lower or "tmall.com" in lower:
        from collectors.adapters.tmall_taobao import TmallTaobaoAdapter

        return not TmallTaobaoAdapter().is_product_url(url)
    return False
