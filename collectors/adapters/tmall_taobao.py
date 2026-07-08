from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


class TmallTaobaoAdapter:
    def supports(self, url: str) -> bool:
        lower = url.lower()
        return "taobao.com" in lower or "tmall.com" in lower

    def detail_api_urls(self, product_url: str, markup: str = "") -> list[str]:
        urls: list[str] = []
        item_id = self._extract_item_id(product_url, markup)
        if item_id:
            urls.extend(
                [
                    f"https://h5api.m.taobao.com/h5/mtop.taobao.detail.getdesc/6.0/?itemNumId={item_id}",
                    f"https://h5api.m.tmall.com/h5/mtop.tmall.detail.getdesc/6.0/?itemNumId={item_id}",
                ]
            )
        for match in re.finditer(
            r"""(?:(?:https?:)?//)[^"'\s>]*(?:getdesc|desc|description|detail\.desc)[^"'\s<]*""",
            markup,
            re.I,
        ):
            value = match.group(0)
            if value.startswith("//"):
                value = "https:" + value
            urls.append(value)
        return list(dict.fromkeys(urls))

    def _extract_item_id(self, product_url: str, markup: str) -> str:
        parsed = urlparse(product_url)
        query = parse_qs(parsed.query)
        for key in ("id", "item_id", "itemId", "itemNumId"):
            value = query.get(key)
            if value and value[0].isdigit():
                return value[0]
        match = re.search(r'"(?:itemId|itemNumId|id)"\s*[:=]\s*"?(\d{5,20})"?', markup, re.I)
        if match:
            return match.group(1)
        return ""
