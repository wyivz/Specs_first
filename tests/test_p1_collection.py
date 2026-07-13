from __future__ import annotations

import unittest
from unittest.mock import patch

from collectors.adapters.tmall_taobao import TmallTaobaoAdapter
from collectors.credentials import (
    JdCredentials,
    TaobaoCredentials,
    extract_m_h5_tk,
    merge_cookie_token,
    parse_cookie_header,
    request_headers_for_url,
)
from collectors.http import HttpClient, SearchResult


class CredentialsTest(unittest.TestCase):
    def test_parse_cookie_header(self) -> None:
        cookies = parse_cookie_header("pt_key=abc; pt_pin=user", ".jd.com")
        self.assertEqual(len(cookies), 2)
        self.assertEqual(cookies[0]["name"], "pt_key")

    def test_extract_m_h5_tk(self) -> None:
        self.assertEqual(extract_m_h5_tk("_m_h5_tk=abc123_1700; other=1"), "abc123_1700")

    def test_merge_cookie_token(self) -> None:
        merged = merge_cookie_token("other=1", "abc123_1700")
        self.assertIn("_m_h5_tk=abc123_1700", merged)
        self.assertIn("other=1", merged)

    def test_jd_credentials_headers(self) -> None:
        creds = JdCredentials(cookie="pt_key=1; pt_pin=2")
        self.assertTrue(creds.configured)
        self.assertIn("Cookie", creds.request_headers())

    @patch("collectors.credentials.load_jd_credentials")
    def test_request_headers_for_jd(self, load_jd) -> None:
        load_jd.return_value = JdCredentials(cookie="pt_key=1")
        headers = request_headers_for_url("https://item.jd.com/123.html")
        self.assertIn("Cookie", headers)


class DuckDuckGoSearchTest(unittest.TestCase):
    def test_search_falls_back_to_ddgs(self) -> None:
        client = HttpClient(retries=0)
        with patch.object(client, "_search_duckduckgo_html", return_value=[]):
            with patch.object(client, "_search_duckduckgo_lite", return_value=[]):
                with patch.object(
                    client,
                    "_search_duckduckgo_ddgs",
                    return_value=[SearchResult(title="JD item", url="https://item.jd.com/1.html", snippet="")],
                ):
                    results = client.search("site:jd.com phone", max_results=3)
        self.assertEqual(len(results), 1)
        self.assertIn("jd.com", results[0].url)

    def test_search_falls_back_to_lite_before_ddgs(self) -> None:
        client = HttpClient(retries=0)
        with patch.object(client, "_search_duckduckgo_html", return_value=[]):
            with patch.object(
                client,
                "_search_duckduckgo_lite",
                return_value=[SearchResult(title="Lite hit", url="https://item.jd.com/2.html", snippet="")],
            ):
                with patch.object(client, "_search_duckduckgo_ddgs") as ddgs:
                    results = client.search("SEL50F12GM", max_results=3)
        self.assertEqual(len(results), 1)
        ddgs.assert_not_called()


class TaobaoMtopP1Test(unittest.TestCase):
    def test_rebuild_signed_url(self) -> None:
        creds = TaobaoCredentials(cookie="_m_h5_tk=abc123_token_1700")
        adapter = TmallTaobaoAdapter(creds)
        original = adapter.build_signed_mtop_url(
            "mtop.taobao.detail.getdesc",
            "6.0",
            {"id": "520813140663"},
            host="h5api.m.tmall.com",
            timestamp_ms=1700000000000,
        )
        rebuilt = adapter._rebuild_signed_url(original)
        self.assertIn("mtop.taobao.detail.getdesc", rebuilt)
        self.assertIn("sign=", rebuilt)
        self.assertNotEqual(rebuilt, original)

    def test_refresh_sign_token_updates_credentials(self) -> None:
        creds = TaobaoCredentials(cookie="other=1; _m_h5_tk=oldtoken_111")
        adapter = TmallTaobaoAdapter(creds)

        class FakeHttp(HttpClient):
            def fetch(self, url: str, *, platform: str = "", extra_headers=None, method: str = "GET", body=None):  # type: ignore[override]
                from collectors.http import FetchResult

                return FetchResult(
                    url=url,
                    status=200,
                    text="window._m_h5_tk='newtoken_222'",
                    content_type="text/html",
                    response_headers={"set-cookie": "_m_h5_tk=newtoken_222; Path=/"},
                )

        adapter._refresh_sign_token(FakeHttp(), "https://detail.tmall.com/item.htm?id=1")
        self.assertEqual(adapter.credentials.sign_token(), "newtoken")

    def test_token_error_retries_with_rebuilt_url(self) -> None:
        creds = TaobaoCredentials(cookie="_m_h5_tk=abc123_token_1700")
        adapter = TmallTaobaoAdapter(creds)
        signed_url = adapter.build_signed_mtop_url(
            "mtop.taobao.detail.getdesc",
            "6.0",
            {"id": "123"},
            host="h5api.m.taobao.com",
            timestamp_ms=1700000000000,
        )
        calls: list[str] = []

        class FakeHttp(HttpClient):
            def fetch(self, url: str, *, platform: str = "", extra_headers=None, method: str = "GET", body=None):  # type: ignore[override]
                from collectors.http import FetchResult

                calls.append(url)
                if len(calls) == 1:
                    return FetchResult(
                        url=url,
                        status=200,
                        text='{"ret":["FAIL_SYS_TOKEN_EXPIRED::令牌过期"],"data":{}}',
                        content_type="application/json",
                        response_headers={},
                    )
                return FetchResult(
                    url=url,
                    status=200,
                    text='{"ret":["SUCCESS::调用成功"],"data":{"price":"99.00"}}',
                    content_type="application/json",
                )

        text = adapter.fetch_mtop_payload(
            FakeHttp(),
            signed_url,
            referer="https://detail.tmall.com/item.htm?id=123",
        )
        self.assertIn("SUCCESS", text)
        self.assertGreaterEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
