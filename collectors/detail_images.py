"""Detail-image URL ranking and authenticated download for Gemini vision."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, url2pathname, urlopen

from collectors.credentials import request_headers_for_url
from collectors.http import DEFAULT_HEADERS


@dataclass(frozen=True)
class DownloadedImage:
    url: str
    data: bytes
    mime_type: str


def path_to_file_url(path: Path | str) -> str:
    return Path(path).resolve().as_uri()


def _mime_from_magic(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def load_local_image(url_or_path: str) -> DownloadedImage | None:
    """Load a local screenshot for vision (file:// URI or filesystem path)."""
    raw = (url_or_path or "").strip()
    if not raw:
        return None
    path: Path | None = None
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        path = Path(url2pathname(unquote(parsed.path)))
    else:
        candidate = Path(raw)
        if candidate.exists() and candidate.is_file():
            path = candidate
    if path is None or not path.exists() or not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) < 256:
        return None
    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix) or _mime_from_magic(data)
    return DownloadedImage(url=path_to_file_url(path), data=data, mime_type=mime)


def infer_image_referer(image_url: str, referer: str = "") -> str:
    if referer and referer.startswith("http"):
        return referer
    lower = (image_url or "").lower()
    if any(hint in lower for hint in ("360buyimg", "jd.com", "jd.hk")):
        return "https://item.jd.com/"
    if any(hint in lower for hint in ("alicdn.com", "taobao.com", "tmall.com")):
        return "https://detail.tmall.com/"
    return ""


def image_request_headers(image_url: str, *, referer: str = "") -> dict[str, str]:
    ref = infer_image_referer(image_url, referer)
    headers = {
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": DEFAULT_HEADERS["Accept-Language"],
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
    if ref:
        headers["Referer"] = ref
    cookie_url = ref or image_url
    lower = (image_url or "").lower()
    if "360buyimg" in lower:
        cookie_url = ref or "https://item.jd.com/"
    elif "alicdn" in lower:
        cookie_url = ref or "https://detail.tmall.com/"
    for key, value in request_headers_for_url(cookie_url, referer=ref).items():
        if value:
            headers[key] = value
    return headers


def download_detail_image(
    url: str,
    *,
    referer: str = "",
    max_bytes: int = 4_000_000,
    attempts: int = 2,
    timeout_seconds: float = 12.0,
) -> DownloadedImage | None:
    """Download an ecommerce detail image with Referer/Cookie and one retry.

    Also accepts ``file://`` URIs / local paths from browser param screenshots.
    """
    if not url:
        return None
    if url.startswith("file:") or (not url.startswith("http") and Path(url).exists()):
        return load_local_image(url)
    if not url.startswith("http"):
        return None
    headers = image_request_headers(url, referer=referer)
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout_seconds) as response:
                data = response.read(max_bytes)
                if not data or len(data) < 256:
                    return None
                mime = response.headers.get_content_type() or ""
                if not mime.startswith("image/"):
                    mime = _mime_from_magic(data)
                return DownloadedImage(url=url, data=data, mime_type=mime)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
    del last_error
    return None
