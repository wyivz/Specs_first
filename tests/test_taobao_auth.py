from __future__ import annotations

import unittest

from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.credentials import TaobaoCredentials
from collectors.http import FetchResult, HttpClient
from collectors.platform_auth import PlatformAuthRequired
from collectors.sources import EcommerceSourceCollector


class TaobaoEcommerceIntegrationTest(unittest.TestCase):
    def test_fetch_detail_payloads_raises_on_mtop_auth_error(self) -> None:
        credentials = TaobaoCredentials(cookie="_m_h5_tk=abc123_token_part_1700; cookie2=1")
        adapter = TmallTaobaoAdapter(credentials)
        signed_url = adapter.build_signed_mtop_url(
            "mtop.taobao.detail.getdesc",
            "6.0",
            {"id": "123"},
            host="h5api.m.taobao.com",
            timestamp_ms=1700000000000,
        )

        class FakeHttp(HttpClient):
            def fetch(self, url: str, *, platform: str = "", extra_headers=None) -> FetchResult:  # type: ignore[override]
                return FetchResult(
                    url=url,
                    status=200,
                    text='{"ret":["FAIL_SYS_SESSION_EXPIRED::Session过期"],"data":{}}',
                    content_type="application/json",
                )

        collector = EcommerceSourceCollector(FakeHttp())
        collector.tmall_taobao = adapter
        with self.assertRaises(PlatformAuthRequired):
            collector._fetch_detail_payloads(
                [signed_url],
                platform="Taobao/Tmall",
                referer_url="https://detail.tmall.com/item.htm?id=123",
                task_id="t1",
                use_browser=False,
                storage_state_path="",
                sku="demo",
            )


if __name__ == "__main__":
    unittest.main()
