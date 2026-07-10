from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from collectors.browser import DESKTOP_UA, PlaywrightCapture, _launch_browser


@dataclass(frozen=True)
class BrowserCaptionPayload:
    language_code: str
    kind: str
    payload: str
    source: str = "browser-fetch"
    fmt: str = ""


_IN_PAGE_CAPTION_SCRIPT = """
async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitForPlayer = async () => {
    for (let attempt = 0; attempt < 24; attempt += 1) {
      const player = window.ytInitialPlayerResponse;
      const tracks = player?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
      if (tracks && tracks.length) {
        return player;
      }
      await sleep(250);
    }
    return window.ytInitialPlayerResponse || null;
  };

  const fetchCaption = async (url) => {
    const formats = ["json3", "srv3", "vtt", null];
    for (const fmt of formats) {
      let requestUrl = url;
      if (fmt) {
        const separator = url.includes("?") ? "&" : "?";
        requestUrl = url.includes("fmt=")
          ? url.replace(/fmt=[^&]+/, `fmt=${fmt}`)
          : `${url}${separator}fmt=${fmt}`;
      }
      try {
        const response = await fetch(requestUrl, { credentials: "include", mode: "cors" });
        if (!response.ok) {
          continue;
        }
        const text = await response.text();
        if (text && text.trim().length > 20) {
          return { payload: text, fmt: fmt || "raw", requestUrl };
        }
      } catch (_error) {
        continue;
      }
    }
    return null;
  };

  const player = await waitForPlayer();
  const tracks = player?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
  const results = [];
  for (const track of tracks) {
    const baseUrl = track.baseUrl || "";
    if (!baseUrl) {
      continue;
    }
    const fetched = await fetchCaption(baseUrl);
    if (fetched) {
      results.push({
        languageCode: track.languageCode || "",
        kind: track.kind || "",
        isTranslatable: !!track.isTranslatable,
        baseUrl,
        payload: fetched.payload,
        fmt: fetched.fmt,
        source: "browser-fetch",
      });
    }
    if (track.isTranslatable) {
      for (const tlang of ["zh-Hans", "zh-Hant", "en"]) {
        const separator = baseUrl.includes("?") ? "&" : "?";
        const translatedUrl = `${baseUrl}${separator}tlang=${tlang}`;
        const translated = await fetchCaption(translatedUrl);
        if (translated) {
          results.push({
            languageCode: tlang,
            kind: track.kind || "",
            isTranslatable: false,
            baseUrl: translatedUrl,
            payload: translated.payload,
            fmt: translated.fmt,
            source: "browser-translate",
          });
        }
      }
    }
  }
  return {
    trackMeta: tracks.map((track) => ({
      languageCode: track.languageCode || "",
      kind: track.kind || "",
      requiresPoToken: (track.baseUrl || "").includes("exp=xpe"),
    })),
    results,
  };
}
"""


def fetch_caption_payloads_in_browser(
    watch_url: str,
    *,
    storage_state_path: Path | None = None,
    output_dir: Path | None = None,
) -> list[BrowserCaptionPayload]:
    """Load a YouTube watch page in Playwright and fetch captions in-page (PoToken-safe)."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install playwright and run `playwright install` before YouTube browser transcripts.") from exc

    base_dir = output_dir or Path("vault_output/browser_captures")
    base_dir.mkdir(parents=True, exist_ok=True)
    resolved_state = storage_state_path or (base_dir / "youtube_transcript_storage_state.json")

    network_payloads: list[BrowserCaptionPayload] = []

    def _on_response(response) -> None:
        url = response.url or ""
        if "timedtext" not in url:
            return
        try:
            if not response.ok:
                return
            body = response.text()
        except Exception:
            return
        if not body or not body.strip():
            return
        network_payloads.append(
            BrowserCaptionPayload(
                language_code=_language_from_timedtext_url(url),
                kind="network",
                payload=body,
                source="browser-network",
            )
        )

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, headless=True, prefer_system_browser=True)
        context_kwargs: dict = {
            "viewport": {"width": 1365, "height": 900},
            "user_agent": DESKTOP_UA,
            "locale": "en-US",
        }
        if resolved_state.exists():
            context_kwargs["storage_state"] = str(resolved_state)
        context = browser.new_context(**context_kwargs)
        PlaywrightCapture._inject_platform_cookies(context, watch_url)
        page = context.new_page()
        page.on("response", _on_response)
        try:
            page.goto(watch_url, wait_until="domcontentloaded", timeout=40_000)
            page.wait_for_timeout(1500)
            raw = page.evaluate(_IN_PAGE_CAPTION_SCRIPT)
            context.storage_state(path=str(resolved_state))
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Timed out while loading YouTube watch page {watch_url}") from exc
        finally:
            browser.close()

    payloads = [*network_payloads]
    if isinstance(raw, dict):
        for item in raw.get("results") or []:
            if not isinstance(item, dict):
                continue
            payload = str(item.get("payload") or "")
            if not payload.strip():
                continue
            payloads.append(
                BrowserCaptionPayload(
                    language_code=str(item.get("languageCode") or ""),
                    kind=str(item.get("kind") or ""),
                    payload=payload,
                    source=str(item.get("source") or "browser-fetch"),
                    fmt=str(item.get("fmt") or ""),
                )
            )
    return _dedupe_payloads(payloads)


def select_browser_transcript(
    payloads: list[BrowserCaptionPayload],
    preferred_languages: tuple[str, ...],
    *,
    parse_payload: Callable[[str], str],
    language_matches: Callable[[str, str], bool],
) -> str:
    if not payloads:
        return ""

    ordered: list[BrowserCaptionPayload] = []
    seen: set[str] = set()
    manual = [item for item in payloads if item.kind.lower() != "asr"]
    generated = [item for item in payloads if item.kind.lower() == "asr"]
    for pool in (manual, generated, payloads):
        for language in preferred_languages:
            for item in pool:
                key = f"{item.language_code}:{item.source}:{len(item.payload)}"
                if key in seen:
                    continue
                if language_matches(item.language_code, language):
                    ordered.append(item)
                    seen.add(key)
    for item in payloads:
        key = f"{item.language_code}:{item.source}:{len(item.payload)}"
        if key not in seen:
            ordered.append(item)
            seen.add(key)

    for item in ordered:
        text = parse_payload(item.payload)
        if text:
            return text
    return ""


def _dedupe_payloads(payloads: list[BrowserCaptionPayload]) -> list[BrowserCaptionPayload]:
    seen: set[str] = set()
    unique: list[BrowserCaptionPayload] = []
    for item in payloads:
        key = f"{item.language_code}:{item.source}:{item.payload[:120]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _language_from_timedtext_url(url: str) -> str:
    if "lang=" not in url:
        return ""
    query = url.split("?", 1)[-1]
    for part in query.split("&"):
        if part.startswith("lang="):
            return part.split("=", 1)[-1]
        if part.startswith("tlang="):
            return part.split("=", 1)[-1]
    return ""


def track_meta_requires_potoken(track_meta: object) -> bool:
    if not isinstance(track_meta, list):
        return False
    return any(isinstance(item, dict) and item.get("requiresPoToken") for item in track_meta)
