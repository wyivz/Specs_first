from __future__ import annotations

from typing import TypeVar

from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.jd import JdAdapter
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.adapters.youtube import YouTubeAdapter
from collectors.adapters.youtube_comments import YouTubeCommentFetcher
from collectors.credentials import load_taobao_credentials
from collectors.diagnostics import CollectorDiagnostics
from collectors.http import HttpClient
from collectors.settings import settings

T = TypeVar("T")


class AdapterRegistry:
    """Plugin-style registry for platform adapters."""

    def __init__(self) -> None:
        self._adapters: list[object] = []

    def register(self, adapter: object) -> None:
        self._adapters.append(adapter)

    def all(self) -> tuple[object, ...]:
        return tuple(self._adapters)

    def for_url(self, url: str) -> object | None:
        for adapter in self._adapters:
            if hasattr(adapter, "supports") and adapter.supports(url):
                return adapter
        return None

    def get(self, adapter_type: type[T]) -> T | None:
        for adapter in self._adapters:
            if isinstance(adapter, adapter_type):
                return adapter
        return None

    def require(self, adapter_type: type[T]) -> T:
        adapter = self.get(adapter_type)
        if adapter is None:
            raise RuntimeError(f"No adapter registered for {adapter_type.__name__}")
        return adapter

    def for_platform(self, platform: str) -> object | None:
        if platform == "JD":
            return self.get(JdAdapter)
        if platform == "Taobao/Tmall":
            return self.get(TmallTaobaoAdapter)
        if platform == "Bilibili":
            return self.get(BilibiliAdapter)
        if platform == "YouTube":
            return self.get(YouTubeAdapter)
        return None


def create_default_registry(
    *,
    http: HttpClient | None = None,
    diagnostics: CollectorDiagnostics | None = None,
) -> AdapterRegistry:
    http = http or HttpClient()
    diagnostics = diagnostics or CollectorDiagnostics()
    registry = AdapterRegistry()
    registry.register(JdAdapter())
    registry.register(TmallTaobaoAdapter(load_taobao_credentials()))
    registry.register(BilibiliAdapter(diagnostics=diagnostics))
    registry.register(
        YouTubeAdapter(
            http,
            diagnostics=diagnostics,
            comment_fetcher=YouTubeCommentFetcher(
                max_comments_per_video=settings.youtube_comment_max_per_video,
                delay_min_seconds=settings.youtube_comment_delay_min,
                delay_max_seconds=settings.youtube_comment_delay_max,
                diagnostics=diagnostics,
            ),
        )
    )
    return registry
