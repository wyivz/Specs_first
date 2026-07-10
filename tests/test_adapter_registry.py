from __future__ import annotations

import unittest

from collectors.adapters.registry import create_default_registry


class AdapterRegistryTest(unittest.TestCase):
    def test_for_url_resolves_jd_and_tmall(self) -> None:
        registry = create_default_registry()
        jd = registry.for_url("https://item.jd.com/123.html")
        assert jd is not None
        self.assertTrue(jd.supports("https://item.jd.com/123.html"))

        tmall = registry.for_url("https://detail.tmall.com/item.htm?id=1")
        assert tmall is not None
        self.assertTrue(tmall.supports("https://detail.tmall.com/item.htm?id=1"))

    def test_get_and_for_platform(self) -> None:
        from collectors.adapters.jd import JdAdapter
        from collectors.adapters.tmall_taobao import TmallTaobaoAdapter

        registry = create_default_registry()
        self.assertIsInstance(registry.get(JdAdapter), JdAdapter)
        self.assertIsInstance(registry.for_platform("Taobao/Tmall"), TmallTaobaoAdapter)


if __name__ == "__main__":
    unittest.main()
