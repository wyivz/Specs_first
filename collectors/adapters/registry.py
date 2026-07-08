from __future__ import annotations

from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.jd import JdAdapter
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.adapters.youtube import YouTubeAdapter


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


def create_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(JdAdapter())
    registry.register(TmallTaobaoAdapter())
    registry.register(BilibiliAdapter())
    registry.register(YouTubeAdapter())
    return registry


DEFAULT_ADAPTER_REGISTRY = create_default_registry()
