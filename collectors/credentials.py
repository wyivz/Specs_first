from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


def parse_cookie_header(cookie: str, domain: str) -> list[dict[str, str]]:
    """Parse ``name=value; ...`` into Playwright cookie dicts."""
    cookies: list[dict[str, str]] = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


def extract_m_h5_tk(raw: str) -> str:
    if not raw:
        return ""
    match = re.search(r"(?:^|;\s*|\"|')_m_h5_tk=([^;\"'\s]+)", raw)
    if match:
        return match.group(1).strip()
    match = re.search(r"_m_h5_tk\s*[=:]\s*['\"]?([^;'\"\s]+)", raw)
    return match.group(1).strip() if match else ""


def merge_cookie_token(cookie: str, m_h5_tk: str) -> str:
    token = m_h5_tk.strip()
    if not token:
        return cookie.strip()
    if not cookie.strip():
        return f"_m_h5_tk={token}"
    if "_m_h5_tk=" in cookie:
        return re.sub(r"_m_h5_tk=[^;]+", f"_m_h5_tk={token}", cookie)
    return f"_m_h5_tk={token}; {cookie.strip()}"


@dataclass(frozen=True)
class BilibiliCredentials:
    sessdata: str
    bili_jct: str
    dedeuserid: str
    buvid3: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.sessdata and self.bili_jct and self.dedeuserid)

    def to_credential(self):
        from bilibili_api import Credential

        return Credential(
            sessdata=self.sessdata,
            bili_jct=self.bili_jct,
            dedeuserid=self.dedeuserid,
            buvid3=self.buvid3 or None,
        )


def load_bilibili_credentials() -> BilibiliCredentials:
    from collectors.settings import settings

    return BilibiliCredentials(
        sessdata=settings.bilibili_sessdata,
        bili_jct=settings.bilibili_bili_jct,
        dedeuserid=settings.bilibili_dedeuserid,
        buvid3=settings.bilibili_buvid3,
    )


@dataclass(frozen=True)
class JdCredentials:
    """JD session cookies (pt_key, pt_pin, etc.) for price/spec microservice access."""

    cookie: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.cookie.strip())

    def request_headers(self) -> dict[str, str]:
        cookie = self.cookie.strip()
        if not cookie:
            return {}
        return {"Cookie": cookie}

    def playwright_cookies(self) -> list[dict[str, str]]:
        return parse_cookie_header(self.cookie, ".jd.com")


def load_jd_credentials() -> JdCredentials:
    from collectors.settings import settings

    return JdCredentials(cookie=settings.jd_cookie)


@dataclass(frozen=True)
class RedditCredentials:
    """Reddit session cookies for authenticated forum reads.

    Copy the full Cookie header from browser DevTools while logged in to
    reddit.com. Typical fields include ``reddit_session`` and ``token_v2``.
    Without cookies, auto ``site:reddit.com`` search is skipped; pasted
    thread URLs still work when Playwright is enabled.
    """

    cookie: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.cookie.strip())

    def request_headers(self) -> dict[str, str]:
        cookie = self.cookie.strip()
        if not cookie:
            return {}
        return {"Cookie": cookie}

    def playwright_cookies(self) -> list[dict[str, str]]:
        return parse_cookie_header(self.cookie, ".reddit.com")


def load_reddit_credentials() -> RedditCredentials:
    from collectors.settings import settings

    return RedditCredentials(cookie=settings.reddit_cookie)


@dataclass(frozen=True)
class TaobaoCredentials:
    """Taobao/Tmall session cookies for mtop H5 API signing.

    Copy the full Cookie header from browser DevTools while logged in to
    taobao.com or tmall.com. Recommended fields include ``_m_h5_tk``,
    ``_m_h5_tk_enc``, ``cna``, and ``isg`` in addition to login cookies.
    """

    cookie: str = ""
    m_h5_tk: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.sign_token())

    def sign_token(self) -> str:
        raw = self.m_h5_tk.strip()
        if not raw and self.cookie:
            raw = extract_m_h5_tk(self.cookie)
        if not raw:
            return ""
        return raw.split("_", 1)[0]

    def cookie_header(self) -> str:
        return self.cookie.strip()

    def request_headers(self, *, referer: str = "") -> dict[str, str]:
        ref = referer.strip()
        if not ref:
            ref = "https://www.taobao.com/"
        parsed = urlparse(ref)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "https://www.taobao.com"
        headers = {
            "Referer": ref,
            "Origin": origin,
        }
        if self.cookie_header():
            headers["Cookie"] = self.cookie_header()
        return headers

    def with_m_h5_tk(self, m_h5_tk: str) -> TaobaoCredentials:
        token = m_h5_tk.strip()
        if not token:
            return self
        return TaobaoCredentials(cookie=merge_cookie_token(self.cookie, token), m_h5_tk=token)

    def playwright_cookies(self, url: str = "") -> list[dict[str, str]]:
        lower = (url or "").lower()
        domain = ".tmall.com" if "tmall.com" in lower else ".taobao.com"
        return parse_cookie_header(self.cookie, domain)


def load_taobao_credentials() -> TaobaoCredentials:
    from collectors.settings import settings

    return TaobaoCredentials(
        cookie=settings.taobao_cookie,
        m_h5_tk=settings.taobao_m_h5_tk,
    )


def request_headers_for_url(url: str, *, referer: str = "") -> dict[str, str]:
    lower = url.lower()
    if "jd.com" in lower or "jd.hk" in lower:
        return load_jd_credentials().request_headers()
    if "taobao.com" in lower or "tmall.com" in lower:
        creds = load_taobao_credentials()
        if creds.configured:
            return creds.request_headers(referer=referer or url)
    if "reddit.com" in lower:
        return load_reddit_credentials().request_headers()
    return {}


def playwright_cookies_for_url(url: str) -> list[dict[str, str]]:
    lower = url.lower()
    if "jd.com" in lower or "jd.hk" in lower:
        return load_jd_credentials().playwright_cookies()
    if "taobao.com" in lower or "tmall.com" in lower:
        creds = load_taobao_credentials()
        if creds.configured:
            return creds.playwright_cookies(url)
    if "reddit.com" in lower:
        creds = load_reddit_credentials()
        if creds.configured:
            return creds.playwright_cookies()
    return []
