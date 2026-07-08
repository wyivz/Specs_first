from __future__ import annotations

import re
from dataclasses import dataclass


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
    from backend.config import settings

    return BilibiliCredentials(
        sessdata=settings.bilibili_sessdata,
        bili_jct=settings.bilibili_bili_jct,
        dedeuserid=settings.bilibili_dedeuserid,
        buvid3=settings.bilibili_buvid3,
    )


@dataclass(frozen=True)
class TaobaoCredentials:
    """Taobao/Tmall session cookies for mtop H5 API signing.

    Copy the full Cookie header from browser DevTools while logged in to
    taobao.com or tmall.com. At minimum ``_m_h5_tk`` is required for sign.
    """

    cookie: str = ""
    m_h5_tk: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.sign_token())

    def sign_token(self) -> str:
        raw = self.m_h5_tk.strip()
        if not raw and self.cookie:
            match = re.search(r"(?:^|;\s*)_m_h5_tk=([^;]+)", self.cookie)
            if match:
                raw = match.group(1).strip()
        if not raw:
            return ""
        return raw.split("_", 1)[0]

    def cookie_header(self) -> str:
        return self.cookie.strip()

    def request_headers(self) -> dict[str, str]:
        headers = {
            "Referer": "https://www.taobao.com/",
            "Origin": "https://www.taobao.com",
        }
        if self.cookie_header():
            headers["Cookie"] = self.cookie_header()
        return headers


def load_taobao_credentials() -> TaobaoCredentials:
    from backend.config import settings

    return TaobaoCredentials(
        cookie=settings.taobao_cookie,
        m_h5_tk=settings.taobao_m_h5_tk,
    )
