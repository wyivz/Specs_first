from __future__ import annotations

import hashlib
import json
import re
import time
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from collectors.credentials import TaobaoCredentials, load_taobao_credentials
from collectors.extractors import ParsedPrice, build_evidence, extract_price
from collectors.http import HttpClient, clip, html_to_text
from collectors.platform_auth import PlatformAuthRequired, is_verification_error
from schemas import PriceFinding

MTOP_AUTH_MARKERS = (
    "FAIL_SYS_TOKEN",
    "FAIL_SYS_SESSION",
    "RGV587",
    "USER_VALIDATE",
    "SESSION_EXPIRED",
    "TOKEN_EMPTY",
    "TOKEN_EXPIRED",
    "AUTH_REJECT",
    "NEED_LOGIN",
)


class TmallTaobaoAdapter:
    APP_KEY = "12574478"
    JSV = "2.7.2"
    PRICE_PATTERNS = [
        re.compile(r'"subPrice"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"price"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"priceText"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r'"promPrice"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"'),
        re.compile(r"券后价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
        re.compile(r"到手价\D{0,12}([0-9]{2,6}(?:\.[0-9]{1,2})?)"),
    ]

    def __init__(self, credentials: TaobaoCredentials | None = None) -> None:
        self.credentials = credentials or load_taobao_credentials()

    def supports(self, url: str) -> bool:
        lower = url.lower()
        return "taobao.com" in lower or "tmall.com" in lower

    def normalize_url(self, url: str) -> str:
        if not self.supports(url):
            return url
        item_id = self._extract_item_id(url, "")
        if not item_id:
            return url
        if "tmall.com" in url.lower():
            return f"https://detail.tmall.com/item.htm?id={item_id}"
        return f"https://item.taobao.com/item.htm?id={item_id}"

    def detail_api_urls(self, product_url: str, markup: str = "") -> list[str]:
        urls: list[str] = []
        item_id = self._extract_item_id(product_url, markup)
        if item_id:
            urls.extend(self._default_detail_urls(item_id, product_url))
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

    def _default_detail_urls(self, item_id: str, product_url: str) -> list[str]:
        host = "h5api.m.tmall.com" if "tmall.com" in product_url.lower() else "h5api.m.taobao.com"
        if self.credentials.configured:
            return [
                self.build_signed_mtop_url(
                    "mtop.taobao.detail.getdesc",
                    "6.0",
                    {"id": item_id},
                    host=host,
                ),
                self.build_signed_mtop_url(
                    "mtop.taobao.detail.getdetail",
                    "6.0",
                    {"itemNumId": item_id},
                    host=host,
                ),
            ]
        return [
            f"https://{host}/h5/mtop.taobao.detail.getdesc/6.0/?itemNumId={item_id}",
            f"https://{host}/h5/mtop.tmall.detail.getdesc/6.0/?itemNumId={item_id}",
        ]

    def build_signed_mtop_url(
        self,
        api: str,
        version: str,
        data: dict[str, Any],
        *,
        host: str = "h5api.m.taobao.com",
        timestamp_ms: int | None = None,
    ) -> str:
        data_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        token = self.credentials.sign_token()
        if not token:
            raise ValueError("Taobao credentials are not configured for mtop signing")
        t = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
        sign = hashlib.md5(f"{token}&{t}&{self.APP_KEY}&{data_str}".encode()).hexdigest()
        query = (
            f"jsv={self.JSV}"
            f"&appKey={self.APP_KEY}"
            f"&t={t}"
            f"&sign={sign}"
            f"&api={quote(api, safe='')}"
            f"&v={version}"
            f"&type=json"
            f"&dataType=json"
            f"&data={quote(data_str, safe='')}"
        )
        return f"https://{host}/h5/{api}/{version}/?{query}"

    def compute_sign(self, token: str, timestamp_ms: int, data: dict[str, Any]) -> str:
        data_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return hashlib.md5(f"{token}&{timestamp_ms}&{self.APP_KEY}&{data_str}".encode()).hexdigest()

    def parse_mtop_json(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if raw[0] not in "{[":
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                return None
            raw = match.group(0)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def inspect_mtop_response(self, text: str, *, url: str = "") -> None:
        lowered = (text or "").lower()
        if is_verification_error(text) or "x5secdata" in lowered or "punish" in lowered:
            raise PlatformAuthRequired(
                platform="Taobao/Tmall",
                message="Taobao/Tmall verification required before continuing",
                url=url or "https://www.taobao.com",
            )
        payload = self.parse_mtop_json(text)
        if not payload:
            return
        ret = payload.get("ret", [])
        ret_text = " ".join(ret) if isinstance(ret, list) else str(ret)
        if "SUCCESS" in ret_text:
            return
        if any(marker in ret_text for marker in MTOP_AUTH_MARKERS):
            raise PlatformAuthRequired(
                platform="Taobao/Tmall",
                message=f"Taobao/Tmall session or captcha required: {ret_text}",
                url=url or "https://www.taobao.com",
            )

    def fetch_mtop_payload(
        self,
        http: HttpClient,
        url: str,
        *,
        referer: str = "",
    ) -> str:
        headers = self.credentials.request_headers()
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
        result = http.fetch(url, extra_headers=headers)
        self.inspect_mtop_response(result.text, url=result.url or url)
        if not result.ok:
            if result.status in {401, 403, 412, 429} or is_verification_error(result.error):
                raise PlatformAuthRequired(
                    platform="Taobao/Tmall",
                    message=result.error or f"HTTP {result.status} from Taobao/Tmall API",
                    url=referer or url,
                )
            return ""
        return result.text

    def unwrap_desc_payload(self, payload: str) -> str:
        """Extract HTML/text from mtop getdesc JSON wrappers."""
        parsed = self.parse_mtop_json(payload)
        if parsed is not None:
            self.inspect_mtop_response(payload)
            data = parsed.get("data")
            if isinstance(data, dict):
                for key in ("pcDescContent", "content", "desc", "wdescContent"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return unescape(value)
            if isinstance(data, str) and data.strip():
                return unescape(data)

        text = payload.strip()
        if not text:
            return ""
        for _ in range(3):
            try:
                node = json.loads(text)
            except json.JSONDecodeError:
                break
            if isinstance(node, dict):
                for key in ("pcDescContent", "content", "desc", "data", "result"):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        text = unescape(value)
                        break
                    if isinstance(value, dict):
                        nested = self.unwrap_desc_payload(json.dumps(value, ensure_ascii=False))
                        if nested:
                            return nested
                break
            break
        return text

    def extract_price(self, markup: str) -> ParsedPrice | None:
        text = html_to_text(self.unwrap_desc_payload(markup) or markup)
        parsed = extract_price(text)
        script_prices = self._extract_script_prices(markup)
        mtop_price = self._extract_price_from_mtop(markup)
        if mtop_price is not None:
            script_prices.append(mtop_price)
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

    def build_price_finding(self, url: str, markup: str, platform: str = "Taobao/Tmall") -> PriceFinding | None:
        parsed = self.extract_price(markup)
        if not parsed:
            return None
        text = clip(html_to_text(self.unwrap_desc_payload(markup) or markup), 360)
        evidence = build_evidence(
            platform=platform,
            url=url,
            author=platform,
            locator="tmall-taobao-adapter-price",
            excerpt=text,
            confidence=0.72 if self.credentials.configured else 0.64,
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

    def maybe_raise_page_auth(self, snapshot_text: str, snapshot_blockers: list, url: str) -> None:
        if any(getattr(blocker, "kind", "") == "auth_or_captcha" for blocker in snapshot_blockers):
            raise PlatformAuthRequired(
                platform="Taobao/Tmall",
                message="Taobao/Tmall page requires captcha or login",
                url=url,
            )
        if is_verification_error(snapshot_text):
            raise PlatformAuthRequired(
                platform="Taobao/Tmall",
                message="Taobao/Tmall verification page detected",
                url=url,
            )

    def _extract_price_from_mtop(self, markup: str) -> float | None:
        payload = self.parse_mtop_json(markup)
        if not payload:
            return None
        prices: list[float] = []
        prices.extend(self._walk_json_prices(payload))
        return min(prices) if prices else None

    def _extract_script_prices(self, markup: str) -> list[float]:
        prices: list[float] = []
        for pattern in self.PRICE_PATTERNS:
            for match in pattern.finditer(markup):
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                if 10 <= value <= 1_000_000:
                    prices.append(value)
        for blob in re.findall(r"<script[^>]*>(.*?)</script>", markup, re.I | re.S):
            if "price" not in blob.lower():
                continue
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
                if key.lower() in {"price", "subprice", "promprice", "pricetext", "priceText"} and isinstance(
                    value, (int, float, str)
                ):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        number = 0
                    if 10 <= number <= 1_000_000:
                        values.append(number)
                values.extend(self._walk_json_prices(value))
        elif isinstance(node, list):
            for item in node:
                values.extend(self._walk_json_prices(item))
        return values

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
