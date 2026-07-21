from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SiteStrategy:
    domain: str
    mode: str
    min_chars: int = 80
    prefer_api: bool = False


SITE_STRATEGIES: tuple[SiteStrategy, ...] = (
    SiteStrategy("api.m.jd.com", "api_first", min_chars=120, prefer_api=True),
    SiteStrategy("jd.com", "http_first", min_chars=120, prefer_api=True),
    SiteStrategy("jd.hk", "http_first", min_chars=120, prefer_api=True),
    SiteStrategy("tmall.com", "api_first", min_chars=140, prefer_api=True),
    SiteStrategy("taobao.com", "api_first", min_chars=140, prefer_api=True),
    SiteStrategy("amazon.", "http_first", min_chars=120),
    SiteStrategy("bilibili.com", "browser_first", min_chars=80, prefer_api=True),
    SiteStrategy("youtube.com", "browser_first", min_chars=80, prefer_api=True),
    SiteStrategy("youtu.be", "browser_first", min_chars=80, prefer_api=True),
    SiteStrategy("chiphell.com", "http_first", min_chars=80),
    # Reddit: prefer Cookie HTTP; escalate to browser only when HTTP is weak/blocked.
    SiteStrategy("reddit.com", "http_first", min_chars=80),
)


def strategy_for_url(url: str) -> SiteStrategy:
    host = urlparse(url).netloc.lower()
    for strategy in SITE_STRATEGIES:
        if strategy.domain in host:
            return strategy
    return SiteStrategy(host or "generic", "http_first", min_chars=80)
