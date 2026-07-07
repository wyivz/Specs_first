from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from collectors.http import extract_title, normalize_whitespace, strip_tags


SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "head",
    "nav",
    "footer",
    "aside",
    "header",
    "iframe",
    "form",
}

NOISE_CLASS_HINTS = re.compile(
    r"(nav|menu|footer|sidebar|advert|ads|cookie|banner|popup|modal|captcha|slider|toolbar|breadcrumb)",
    re.I,
)

AUTH_MARKERS = [
    "captcha",
    "recaptcha",
    "hcaptcha",
    "verify you are human",
    "are you a robot",
    "access denied",
    "security check",
    "验证",
    "滑块",
    "安全检测",
    "人机验证",
    "请登录",
    "登录后",
    "sign in to continue",
]

CSS_NOISE_MARKERS = [
    "@media",
    "@keyframes",
    "{color:",
    "font-family:",
    "display:none",
    "visibility:hidden",
]


@dataclass(frozen=True)
class PageBlocker:
    kind: str
    detail: str


@dataclass
class SanitizedPage:
    url: str
    title: str
    text: str
    json_ld: list[dict] = field(default_factory=list)
    meta_description: str = ""
    blockers: list[PageBlocker] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def rich_text(self) -> str:
        parts = [self.title, self.meta_description, self.text]
        for item in self.json_ld:
            parts.append(json.dumps(item, ensure_ascii=False))
        return normalize_whitespace(" ".join(part for part in parts if part))


def sanitize_html(url: str, markup: str) -> SanitizedPage:
    title = extract_title(markup)
    meta_description = _extract_meta_description(markup)
    json_ld = extract_json_ld_objects(markup)
    text = extract_readable_text(markup)
    blockers = detect_page_blockers(url, markup, text, title)
    return SanitizedPage(
        url=url,
        title=title,
        text=text,
        json_ld=json_ld,
        meta_description=meta_description,
        blockers=blockers,
    )


def extract_readable_text(markup: str) -> str:
    from collectors.http import TextExtractor

    class ReadableExtractor(TextExtractor):
        def __init__(self) -> None:
            super().__init__()
            self._skip_stack: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attrs_dict = dict(attrs)
            class_value = " ".join(
                value for key, value in attrs_dict.items() if key == "class" and value
            )
            id_value = attrs_dict.get("id") or ""
            hidden = attrs_dict.get("aria-hidden") == "true" or attrs_dict.get("hidden") is not None
            if hidden or NOISE_CLASS_HINTS.search(f"{class_value} {id_value}") or tag in SKIP_TAGS:
                self._skip_stack.append(tag)
                self._skip_depth += 1
                return
            if tag == "a":
                self._current_href = attrs_dict.get("href")
                self._current_link_text = []

        def handle_endtag(self, tag: str) -> None:
            if self._skip_stack and tag == self._skip_stack[-1]:
                self._skip_stack.pop()
                self._skip_depth -= 1
                return
            if self._skip_depth:
                return
            if tag == "a" and self._current_href:
                text = normalize_whitespace(" ".join(self._current_link_text))
                if text:
                    self.links.append((text, self._current_href))
                self._current_href = None
                self._current_link_text = []

    parser = ReadableExtractor()
    parser.feed(markup)
    text = normalize_whitespace(" ".join(parser.parts))
    text = _strip_css_noise(text)
    return text


def extract_json_ld_objects(markup: str) -> list[dict]:
    objects: list[dict] = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        markup,
        re.I | re.S,
    ):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        elif isinstance(payload, list):
            objects.extend(item for item in payload if isinstance(item, dict))
    return objects


def detect_page_blockers(url: str, markup: str, text: str, title: str = "") -> list[PageBlocker]:
    blockers: list[PageBlocker] = []
    combined = f"{title} {text} {markup[:8000]}".lower()
    for marker in AUTH_MARKERS:
        if marker.lower() in combined:
            blockers.append(PageBlocker("auth_or_captcha", marker))
            break
    if re.search(r"(recaptcha|hcaptcha|geetest|cf-challenge)", markup, re.I):
        blockers.append(PageBlocker("auth_or_captcha", "captcha widget detected"))
    if _is_low_signal_text(text):
        blockers.append(PageBlocker("low_signal", "extracted text too short or noisy"))
    if re.search(r"\b403\b|\b429\b|access denied|forbidden", combined):
        blockers.append(PageBlocker("http_blocked", "access denied or rate limited"))
    return blockers


def is_usable_page(page: SanitizedPage, min_chars: int = 80) -> bool:
    if any(blocker.kind == "auth_or_captcha" for blocker in page.blockers):
        return False
    rich = page.rich_text
    return len(rich) >= min_chars and not _looks_like_css_dump(rich)


def _extract_meta_description(markup: str) -> str:
    match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        markup,
        re.I | re.S,
    )
    if match:
        return normalize_whitespace(strip_tags(match.group(1)))
    return ""


def _strip_css_noise(text: str) -> str:
    if not text:
        return ""
    if sum(marker in text for marker in CSS_NOISE_MARKERS) >= 2:
        text = re.sub(r"\{[^}]{0,300}\}", " ", text)
    text = re.sub(r"@[a-z-]+\s*\{[^}]+\}", " ", text, flags=re.I)
    return normalize_whitespace(text)


def _is_low_signal_text(text: str, min_chars: int = 80) -> bool:
    cleaned = normalize_whitespace(text)
    if len(cleaned) < min_chars:
        return True
    return _looks_like_css_dump(cleaned)


def _looks_like_css_dump(text: str) -> bool:
    brace_ratio = text.count("{") + text.count("}")
    semicolon_ratio = text.count(";")
    if brace_ratio > 12 or semicolon_ratio > 40:
        return True
    alpha = sum(char.isalpha() for char in text)
    return alpha / max(len(text), 1) < 0.2
