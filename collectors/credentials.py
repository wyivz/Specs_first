from __future__ import annotations

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
