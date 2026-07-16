from collectors.adapters.bilibili import BilibiliAdapter
from collectors.adapters.jd import JdAdapter
from collectors.adapters.registry import AdapterRegistry, create_default_registry
from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.adapters.youtube import YouTubeAdapter

__all__ = [
    "AdapterRegistry",
    "BilibiliAdapter",
    "JdAdapter",
    "TmallTaobaoAdapter",
    "YouTubeAdapter",
    "create_default_registry",
]
