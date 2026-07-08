from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field


@dataclass
class PlatformRateLimiter:
    """Serializes outbound platform requests with per-platform minimum spacing."""

    default_interval_seconds: float = 1.0
    platform_intervals: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _last_request_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def wait(self, platform: str, *, jitter: tuple[float, float] = (0.0, 0.0)) -> None:
        interval = self.platform_intervals.get(platform, self.default_interval_seconds)
        with self._lock:
            now = time.monotonic()
            last = self._last_request_at.get(platform, 0.0)
            delay = max(0.0, interval - (now - last))
            if jitter[1] > 0:
                delay += random.uniform(jitter[0], jitter[1])
            if delay:
                time.sleep(delay)
            self._last_request_at[platform] = time.monotonic()


@dataclass
class CollectionGuard:
    """Ensures collection calls run one-at-a-time (no multi-threaded scraping)."""

    _global_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __enter__(self) -> CollectionGuard:
        acquired = self._global_lock.acquire(blocking=False)
        if not acquired:
            raise RuntimeError("Another collection operation is already in progress.")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._global_lock.release()


_default_limiter: PlatformRateLimiter | None = None
_default_guard: CollectionGuard | None = None


def platform_for_url(url: str) -> str:
    lowered = url.lower()
    if "bilibili.com" in lowered:
        return "bilibili"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if any(host in lowered for host in ("jd.com", "jd.hk", "taobao.com", "tmall.com")):
        return "ecommerce"
    return "http"


def get_rate_limiter() -> PlatformRateLimiter:
    global _default_limiter
    if _default_limiter is None:
        from backend.config import settings

        _default_limiter = PlatformRateLimiter(
            default_interval_seconds=settings.collection_min_interval_seconds,
            platform_intervals={
                "bilibili": settings.bilibili_comment_page_delay_seconds,
                "youtube": settings.youtube_comment_delay_min,
                "ecommerce": settings.collection_min_interval_seconds,
                "http": settings.collection_min_interval_seconds,
            },
        )
    return _default_limiter


def get_collection_guard() -> CollectionGuard:
    global _default_guard
    if _default_guard is None:
        _default_guard = CollectionGuard()
    return _default_guard
