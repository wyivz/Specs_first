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


def is_noisy_ecommerce_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    if "{keyword}" in url or "{" in urlparse(url).path:
        return True
    lower = url.lower()
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


def _is_marketplace_non_product(url: str) -> bool:
    lower = url.lower()
    if "jd.com" in lower or "jd.hk" in lower:
        from collectors.adapters.jd import JdAdapter

        return not JdAdapter().is_product_url(url)
    if "taobao.com" in lower or "tmall.com" in lower:
        from collectors.adapters.tmall_taobao import TmallTaobaoAdapter

        return not TmallTaobaoAdapter().is_product_url(url)
    return False
