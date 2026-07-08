from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from collectors.extractors import ParsedPrice, build_evidence, extract_price
from collectors.http import clip, html_to_text
from schemas import EvidenceItem, PriceFinding


class JdAdapter:
    JD_PRICE_PATTERNS = [
        re.compile(r'"op"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"finalPrice"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"price"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r"到手价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
        re.compile(r"券后价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
    ]

    def supports(self, url: str) -> bool:
        lower = url.lower()
        return "jd.com" in lower or "jd.hk" in lower

    def normalize_url(self, url: str) -> str:
        if not self.supports(url):
            return url
        match = re.search(r"item\.jd\.com/(\d+)\.html", url)
        if match:
            return f"https://item.jd.com/{match.group(1)}.html"
        return url

    def detail_api_urls(self, product_url: str, markup: str = "") -> list[str]:
        urls: list[str] = []
        sku_id = self._extract_sku_id(product_url, markup)
        if sku_id:
            urls.extend(
                [
                    f"https://cd.jd.com/description/channel?skuId={sku_id}&mainSkuId={sku_id}",
                    f"https://dx.3.cn/desc/{sku_id}",
                ]
            )
        for match in re.finditer(r"""(?:(?:https?:)?//)[^"'\s>]*(?:getdesc|desc|description|detail)[^"'\s<]*""", markup, re.I):
            value = match.group(0)
            if value.startswith("//"):
                value = "https:" + value
            urls.append(value)
        return list(dict.fromkeys(urls))

    def extract_price(self, markup: str) -> ParsedPrice | None:
        text = html_to_text(markup)
        parsed = extract_price(text)
        script_prices = self._extract_script_prices(markup)
        if script_prices and parsed:
            final = min(script_prices + [parsed.final_price])
            return ParsedPrice(
                list_price=max(parsed.list_price, final),
                coupon_discount=parsed.coupon_discount,
                subsidy_discount=parsed.subsidy_discount,
                cross_store_discount=parsed.cross_store_discount,
                final_price=final,
            )
        if script_prices:
            final = min(script_prices)
            return ParsedPrice(final, 0, 0, 0, final)
        return parsed

    def build_price_finding(self, url: str, markup: str, platform: str = "JD") -> PriceFinding | None:
        parsed = self.extract_price(markup)
        if not parsed:
            return None
        text = clip(html_to_text(markup), 360)
        evidence = build_evidence(
            platform=platform,
            url=url,
            author=platform,
            locator="jd-adapter-price",
            excerpt=text,
            confidence=0.66,
        )
        return PriceFinding(
            platform=platform,
            list_price=parsed.list_price,
            coupon_discount=parsed.coupon_discount,
            subsidy_discount=parsed.subsidy_discount,
            cross_store_discount=parsed.cross_store_discount,
            final_price=parsed.final_price,
            screenshot_path="",
            captured_at=evidence.captured_at,
            evidence=evidence,
        )

    def _extract_script_prices(self, markup: str) -> list[float]:
        prices: list[float] = []
        for pattern in self.JD_PRICE_PATTERNS:
            for match in pattern.finditer(markup):
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                if 100 <= value <= 1_000_000:
                    prices.append(value)
        for blob in re.findall(r"<script[^>]*>(.*?)</script>", markup, re.I | re.S):
            if "price" not in blob.lower():
                continue
            for match in re.finditer(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?', blob, re.I):
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                if 100 <= value <= 1_000_000:
                    prices.append(value)
            try:
                payload = json.loads(blob.strip())
            except json.JSONDecodeError:
                continue
            prices.extend(self._walk_json_prices(payload))
        return sorted(set(prices))

    def _walk_json_prices(self, node: object) -> list[float]:
        values: list[float] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in {"price", "finalprice", "op", "jdprice"} and isinstance(value, (int, float, str)):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        number = 0
                    if 100 <= number <= 1_000_000:
                        values.append(number)
                values.extend(self._walk_json_prices(value))
        elif isinstance(node, list):
            for item in node:
                values.extend(self._walk_json_prices(item))
        return values

    def _extract_sku_id(self, product_url: str, markup: str = "") -> str:
        match = re.search(r"item\.jd\.com/(\d+)\.html", product_url, re.I)
        if match:
            return match.group(1)
        parsed = urlparse(product_url)
        query = parse_qs(parsed.query)
        for key in ("sku", "skuId", "id"):
            value = query.get(key)
            if value and value[0].isdigit():
                return value[0]
        match = re.search(r'"(?:skuId|sku_id)"\s*[:=]\s*"?(\d{4,20})"?', markup, re.I)
        if match:
            return match.group(1)
        return ""
