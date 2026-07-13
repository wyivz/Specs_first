from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field

from collectors.diagnostics import CollectorDiagnostics
from collectors.http import clip
from collectors.rate_limit import PlatformRateLimiter, get_rate_limiter
from schemas.category_profile import real_world_issue_patterns, review_content_patterns


@dataclass
class YouTubeCommentFetcher:
    max_comments_per_video: int = 20
    delay_min_seconds: float = 1.0
    delay_max_seconds: float = 3.0
    rate_limiter: PlatformRateLimiter | None = None
    diagnostics: CollectorDiagnostics | None = None
    _hint_pattern: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.rate_limiter = self.rate_limiter or get_rate_limiter()
        self._hint_pattern = re.compile(
            "|".join(real_world_issue_patterns() + review_content_patterns()),
            re.I,
        )

    def fetch_comment_texts(self, url: str, *, video_id: str = "") -> list[str]:
        self.rate_limiter.wait(  # type: ignore[union-attr]
            "youtube",
            jitter=(0.0, max(0.0, self.delay_max_seconds - self.delay_min_seconds)),
        )
        try:
            from youtube_comment_downloader import YoutubeCommentDownloader
        except ImportError:
            self._record("youtube", "youtube-comment-downloader not installed; skipping API comments", level="info")
            return []

        watch_url = url
        if video_id and "v=" not in url:
            watch_url = f"https://www.youtube.com/watch?v={video_id}"

        comments: list[str] = []
        try:
            downloader = YoutubeCommentDownloader()
            for index, item in enumerate(
                downloader.get_comments_from_url(
                    watch_url,
                    sort_by=0,
                    sleep=max(0.1, self.delay_min_seconds / 2),
                )
            ):
                if index >= self.max_comments_per_video:
                    break
                text = (item.get("text") or "").strip()
                if text:
                    comments.append(text)
        except Exception as exc:
            self._record(
                "youtube",
                f"comment downloader failed for {watch_url}: {exc}; falling back to transcript-only",
            )
            return []

        if self.delay_max_seconds > 0:
            time.sleep(random.uniform(self.delay_min_seconds, self.delay_max_seconds))
        return comments

    def select_review_comments(self, comments: list[str], *, limit: int = 8) -> list[str]:
        matched = [comment for comment in comments if self._hint_pattern.search(comment)]
        return [clip(text, 360) for text in matched[:limit]]

    def _record(self, source: str, message: str, *, level: str = "warning") -> None:
        if self.diagnostics:
            self.diagnostics.record(source, message, level=level)
