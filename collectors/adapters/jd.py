from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from collectors.extractors import ParsedPrice, build_evidence, extract_price, is_plausible_price
from collectors.http import HttpClient, clip, html_to_text
from schemas import EvidenceItem, PriceFinding


class JdAdapter:
    JD_PRICE_PATTERNS = [
        re.compile(r'"op"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"finalPrice"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"price"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r"到手价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
        re.compile(r"券后价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
    ]
    PRODUCT_URL_RE = re.compile(
        r"https?://(?:item\.m\.jd\.com|item\.jd\.com|npcitem\.jd\.hk)/(?:product/)?(\d+)(?:\.html)?",
        re.I,
    )
    NOISE_HOST_HINTS = (
        "campus.jd.com",
        "music.jd.com",
        "ir.jd.com",
        "club.jd.com",
        "passport.jd.com",
        "search.jd.com",
        "pc-frequent-pro.pf.jd.com",
        "pc-frequent.pf.jd.com",
        "frequent.jd.com",
        "jd.com/brand/",
        "jd.com/jiage/",
        "jd.com/hprm/",
    )

    def supports(self, url: str) -> bool:
        lower = url.lower()
        return "jd.com" in lower or "jd.hk" in lower

    def is_product_url(self, url: str) -> bool:
        if not url or "{keyword}" in url or "{" in url:
            return False
        lower = url.lower()
        if any(hint in lower for hint in self.NOISE_HOST_HINTS):
            return False
        return bool(self.PRODUCT_URL_RE.search(url))

    def normalize_url(self, url: str) -> str:
        if not self.supports(url):
            return url
        match = self.PRODUCT_URL_RE.search(url)
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

    def extract_price(self, markup: str, *, sku_id: str = "") -> ParsedPrice | None:
        text = html_to_text(markup)
        parsed = extract_price(text)
        explicit_final = self._extract_explicit_final_price(markup)
        script_prices = self._filter_price_candidates(self._extract_script_prices(markup), sku_id)
        if explicit_final is not None and not self._price_looks_like_sku_fragment(explicit_final, sku_id):
            list_price = max(script_prices + [explicit_final, parsed.list_price if parsed else explicit_final])
            candidate = ParsedPrice(list_price, 0, 0, 0, explicit_final)
            return self._sanitize_parsed_price(candidate, sku_id)
        if script_prices:
            final = self._pick_main_script_price(script_prices)
            list_price = max(script_prices + [final])
            if parsed and parsed.final_price >= final * 0.5:
                candidate = ParsedPrice(
                    max(parsed.list_price, list_price),
                    parsed.coupon_discount,
                    parsed.subsidy_discount,
                    parsed.cross_store_discount,
                    parsed.final_price,
                )
            else:
                candidate = ParsedPrice(list_price, 0, 0, 0, final)
            return self._sanitize_parsed_price(candidate, sku_id)
        return self._sanitize_parsed_price(parsed, sku_id)

    def fetch_price_from_mgets(self, http: HttpClient, sku_id: str) -> ParsedPrice | None:
        if not sku_id:
            return None
        from collectors.credentials import load_jd_credentials

        url = f"https://p.3.cn/prices/mgets?skuIds=J_{sku_id}"
        result = http.fetch(url, extra_headers=load_jd_credentials().request_headers())
        if not result.ok or not result.text.strip():
            return None
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list) or not payload:
            return None
        item = payload[0]
        if not isinstance(item, dict):
            return None
        final = self._to_price(item.get("p"))
        original = self._to_price(item.get("op")) or final
        if final is None:
            return None
        list_price = max(original, final)
        return ParsedPrice(list_price, max(0.0, list_price - final), 0, 0, final)

    def build_price_finding(
        self,
        url: str,
        markup: str,
        platform: str = "JD",
        *,
        http: HttpClient | None = None,
        trace=None,
        sku: str = "",
    ) -> PriceFinding | None:
        sku_id = self._extract_sku_id(url, markup)
        parsed = None
        source = "html"
        if http and sku_id:
            parsed = self.fetch_price_from_mgets(http, sku_id)
            if parsed:
                source = "mgets"
        if not parsed:
            parsed = self.extract_price(markup, sku_id=sku_id)
            source = "html"
        parsed = self._sanitize_parsed_price(parsed, sku_id)
        if not parsed:
            if trace:
                trace.log_price(platform, url, source="none", detail="no price parsed", sku=sku)
            return None
        if trace:
            trace.log_price(
                platform,
                url,
                source=source,
                list_price=parsed.list_price,
                final_price=parsed.final_price,
                detail=f"sku_id={sku_id or '-'}",
                sku=sku,
            )
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

    def _extract_explicit_final_price(self, markup: str) -> float | None:
        for pattern in (
            re.compile(r"到手价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
            re.compile(r"券后价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
        ):
            values: list[float] = []
            for match in pattern.finditer(markup):
                value = self._to_price(match.group(1))
                if value is not None:
                    values.append(value)
            if values:
                return max(values)
        candidates: list[float] = []
        for pattern in (
            re.compile(r'"finalPrice"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?', re.I),
            re.compile(r'"jdPrice"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?', re.I),
        ):
            for match in pattern.finditer(markup):
                value = self._to_price(match.group(1))
                if value is not None:
                    candidates.append(value)
        if candidates:
            return max(candidates)
        return None

    def _pick_main_script_price(self, prices: list[float]) -> float:
        if not prices:
            return 0.0
        if len(prices) == 1:
            return prices[0]
        # Avoid picking accessory/noise prices far below the main cluster.
        sorted_prices = sorted(prices)
        return sorted_prices[-1]

    @staticmethod
    def _to_price(raw: object) -> float | None:
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if is_plausible_price(value):
            return value
        return None

    @staticmethod
    def _price_looks_like_sku_fragment(price: float, sku_id: str) -> bool:
        if not sku_id or price <= 0:
            return False
        token = str(int(round(price)))
        if token in sku_id:
            return True
        for width in range(len(token), len(sku_id) + 1):
            if token.zfill(width) in sku_id:
                return True
        return False

    def _filter_price_candidates(self, prices: list[float], sku_id: str) -> list[float]:
        return [
            price
            for price in prices
            if is_plausible_price(price) and not self._price_looks_like_sku_fragment(price, sku_id)
        ]

    def _sanitize_parsed_price(self, parsed: ParsedPrice | None, sku_id: str) -> ParsedPrice | None:
        if parsed is None:
            return None
        final = parsed.final_price
        list_price = parsed.list_price
        if not is_plausible_price(final):
            return None
        if self._price_looks_like_sku_fragment(final, sku_id):
            return None
        if self._price_looks_like_sku_fragment(list_price, sku_id):
            list_price = final
        if final < list_price * 0.2:
            return None
        if list_price < final:
            list_price = final
        return ParsedPrice(
            list_price,
            parsed.coupon_discount,
            parsed.subsidy_discount,
            parsed.cross_store_discount,
            final,
        )

    def _extract_sku_id(self, product_url: str, markup: str = "") -> str:
        match = self.PRODUCT_URL_RE.search(product_url)
        if match:
            return match.group(1)
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
