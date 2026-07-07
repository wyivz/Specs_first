from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 SpecsFirst/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    text: str
    content_type: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400 and not self.error


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class HttpClient:
    def __init__(self, timeout_seconds: float = 12, retries: int = 2, sleep_seconds: float = 0.5) -> None:
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.sleep_seconds = sleep_seconds

    def fetch(self, url: str) -> FetchResult:
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, headers=DEFAULT_HEADERS)
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read(2_000_000)
                    content_type = response.headers.get("content-type", "")
                    charset = response.headers.get_content_charset() or "utf-8"
                    return FetchResult(
                        url=response.geturl(),
                        status=response.status,
                        text=raw.decode(charset, errors="replace"),
                        content_type=content_type,
                    )
            except HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.reason}"
                if exc.code in {401, 403, 404}:
                    break
            except (TimeoutError, URLError, OSError) as exc:
                last_error = str(exc)
            if attempt < self.retries:
                time.sleep(self.sleep_seconds * (attempt + 1))
        return FetchResult(url=url, status=0, text="", content_type="", error=last_error)

    def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        result = self.fetch(url)
        if not result.ok:
            return []
        return parse_duckduckgo_results(result.text)[:max_results]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "a":
            attrs_dict = dict(attrs)
            self._current_href = attrs_dict.get("href")
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._current_href:
            text = normalize_whitespace(" ".join(self._current_link_text))
            if text:
                self.links.append((text, self._current_href))
            self._current_href = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = normalize_whitespace(data)
        if not text:
            return
        self.parts.append(text)
        if self._current_href:
            self._current_link_text.append(text)


def html_to_text(markup: str) -> str:
    parser = TextExtractor()
    parser.feed(markup)
    return normalize_whitespace(" ".join(parser.parts))


def extract_title(markup: str, fallback: str = "") -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.I | re.S)
    if match:
        return normalize_whitespace(html.unescape(strip_tags(match.group(1))))
    return fallback


def parse_duckduckgo_results(markup: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        markup,
        re.I | re.S,
    ):
        url = html.unescape(match.group(1))
        if "uddg=" in url:
            url_match = re.search(r"uddg=([^&]+)", url)
            if url_match:
                from urllib.parse import unquote

                url = unquote(url_match.group(1))
        results.append(
            SearchResult(
                title=normalize_whitespace(html.unescape(strip_tags(match.group(2)))),
                url=url,
                snippet=normalize_whitespace(html.unescape(strip_tags(match.group(3)))),
            )
        )
    if results:
        return dedupe_results(results)

    extractor = TextExtractor()
    extractor.feed(markup)
    for title, url in extractor.links:
        if url.startswith("http") and len(title) > 8:
            results.append(SearchResult(title=title, url=url, snippet=""))
    return dedupe_results(results)


def dedupe_results(results: Iterable[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)
    return deduped


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clip(value: str, limit: int = 320) -> str:
    value = normalize_whitespace(value)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."
