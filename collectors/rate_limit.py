from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class PlatformRateLimiter:
    """Serializes outbound platform requests with per-platform minimum spacing."""

    default_interval_seconds: float = 1.0
    platform_intervals: dict[str, float] = field(default_factory=dict)
    default_jitter: tuple[float, float] = (0.0, 0.0)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _last_request_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def wait(
        self,
        platform: str,
        *,
        jitter: tuple[float, float] | None = None,
    ) -> None:
        interval = self.platform_intervals.get(platform, self.default_interval_seconds)
        jitter_range = self.default_jitter if jitter is None else jitter
        with self._lock:
            now = time.monotonic()
            last = self._last_request_at.get(platform, 0.0)
            delay = max(0.0, interval - (now - last))
            if jitter_range[1] > 0:
                delay += random.uniform(jitter_range[0], jitter_range[1])
        # Sleep outside the lock so different platforms can pace in parallel.
        if delay:
            time.sleep(delay)
        with self._lock:
            self._last_request_at[platform] = time.monotonic()


@dataclass
class HostBackoffTracker:
    """Exponential cooldown after rate-limit / repeated soft failures (per site family)."""

    base_seconds: float = 30.0
    max_seconds: float = 120.0
    soft_fail_limit: int = 3
    soft_fail_cooldown_seconds: float = 90.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _until: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _strikes: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _soft_strikes: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    @staticmethod
    def family_key(url: str) -> str:
        host = (urlparse(url).netloc or "").lower()
        if any(token in host for token in ("jd.com", "jd.hk", "360buyimg")):
            return "jd.com"
        if any(token in host for token in ("taobao.com", "tmall.com", "alicdn.com")):
            return "taobao.com"
        if "bilibili.com" in host:
            return "bilibili.com"
        if "youtube.com" in host or host.endswith("youtu.be"):
            return "youtube.com"
        if "reddit.com" in host:
            return "reddit.com"
        if "chiphell.com" in host:
            return "chiphell.com"
        return host or "unknown"

    def note_rate_limited(self, url: str) -> float:
        """Record a strike and return the cooldown seconds applied."""
        key = self.family_key(url)
        with self._lock:
            strikes = self._strikes.get(key, 0) + 1
            self._strikes[key] = strikes
            cooldown = min(self.max_seconds, self.base_seconds * (2 ** max(0, strikes - 1)))
            self._until[key] = time.monotonic() + cooldown
            return cooldown

    def note_soft_failure(self, url: str, kind: str = "") -> float:
        """After N captcha/undecoded/low_signal hits, skip the host family briefly."""
        del kind
        key = self.family_key(url)
        with self._lock:
            strikes = self._soft_strikes.get(key, 0) + 1
            self._soft_strikes[key] = strikes
            if strikes < self.soft_fail_limit:
                return 0.0
            cooldown = self.soft_fail_cooldown_seconds
            self._until[key] = max(self._until.get(key, 0.0), time.monotonic() + cooldown)
            return cooldown

    def note_success(self, url: str) -> None:
        key = self.family_key(url)
        with self._lock:
            self._soft_strikes.pop(key, None)

    def remaining_seconds(self, url: str) -> float:
        key = self.family_key(url)
        with self._lock:
            until = self._until.get(key, 0.0)
            return max(0.0, until - time.monotonic())

    def wait_if_needed(self, url: str) -> float:
        remaining = self.remaining_seconds(url)
        if remaining > 0:
            time.sleep(remaining)
            key = self.family_key(url)
            with self._lock:
                if self._until.get(key, 0.0) <= time.monotonic():
                    self._until.pop(key, None)
        return remaining

    def in_backoff(self, url: str) -> bool:
        return self.remaining_seconds(url) > 0

    def should_skip_host(self, url: str) -> bool:
        return self.in_backoff(url)

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
_default_host_backoff: HostBackoffTracker | None = None


def platform_for_url(url: str) -> str:
    lowered = url.lower()
    if "bilibili.com" in lowered:
        return "bilibili"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if any(host in lowered for host in ("jd.com", "jd.hk", "taobao.com", "tmall.com")):
        return "ecommerce"
    return "http"


def human_pause(min_seconds: float = 0.5, max_seconds: float = 2.0) -> None:
    """Short think-time between SKUs / platforms."""
    lo = max(0.0, min_seconds)
    hi = max(lo, max_seconds)
    time.sleep(random.uniform(lo, hi))


def get_rate_limiter() -> PlatformRateLimiter:
    global _default_limiter
    if _default_limiter is None:
        from collectors.settings import settings

        _default_limiter = PlatformRateLimiter(
            default_interval_seconds=settings.collection_min_interval_seconds,
            platform_intervals={
                "bilibili": settings.bilibili_comment_page_delay_seconds,
                "youtube": settings.youtube_comment_delay_min,
                "ecommerce": settings.ecommerce_min_interval_seconds,
                "http": settings.collection_min_interval_seconds,
            },
            default_jitter=(0.3, 1.2),
        )
    return _default_limiter


def get_host_backoff() -> HostBackoffTracker:
    global _default_host_backoff
    if _default_host_backoff is None:
        _default_host_backoff = HostBackoffTracker()
    return _default_host_backoff


def reset_host_backoff_for_tests() -> None:
    global _default_host_backoff
    _default_host_backoff = HostBackoffTracker()


def get_collection_guard() -> CollectionGuard:
    global _default_guard
    if _default_guard is None:
        _default_guard = CollectionGuard()
    return _default_guard
