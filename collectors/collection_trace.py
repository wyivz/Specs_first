from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from collectors.diagnostics import CollectorDiagnostics


@dataclass
class CollectionTrace:
    """Append human-readable collection logs to diagnostics and an optional file."""

    diagnostics: CollectorDiagnostics
    log_path: Path | None = None
    task_id: str = ""
    _lines: list[str] = field(default_factory=list, init=False, repr=False)

    def log(self, source: str, message: str, *, sku: str = "", level: str = "trace") -> None:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        task = f" task={self.task_id}" if self.task_id else ""
        sku_part = f" sku={sku}" if sku else ""
        line = f"{stamp}{task}{sku_part} [{source}] {message}"
        self._lines.append(line)
        self.diagnostics.record(source, message, level=level, sku=sku)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def log_fetch(
        self,
        url: str,
        *,
        method: str,
        status: int = 0,
        ok: bool = False,
        text_len: int = 0,
        preview: str = "",
        sku: str = "",
        error: str = "",
    ) -> None:
        parts = [f"url={url}", f"method={method}", f"ok={ok}", f"text_len={text_len}"]
        if status:
            parts.append(f"status={status}")
        if error:
            parts.append(f"error={error}")
        if preview:
            parts.append(f"preview={preview[:240]}")
        self.log("fetch", " | ".join(parts), sku=sku)

    def log_price(
        self,
        platform: str,
        url: str,
        *,
        source: str,
        list_price: float | None = None,
        final_price: float | None = None,
        detail: str = "",
        sku: str = "",
    ) -> None:
        parts = [f"platform={platform}", f"url={url}", f"source={source}"]
        if list_price is not None:
            parts.append(f"list={list_price}")
        if final_price is not None:
            parts.append(f"final={final_price}")
        if detail:
            parts.append(detail)
        self.log("price", " | ".join(parts), sku=sku, level="info")

    def log_spec(self, source: str, message: str, *, sku: str = "") -> None:
        self.log("spec", message, sku=sku, level="info")

    def log_bilibili(self, bvid: str, *, title: str = "", subtitle_len: int = 0, comments: int = 0, note: str = "", sku: str = "") -> None:
        parts = [f"bvid={bvid}"]
        if title:
            parts.append(f"title={title[:120]}")
        parts.append(f"subtitle_len={subtitle_len}")
        parts.append(f"comments={comments}")
        if note:
            parts.append(note)
        self.log("bilibili", " | ".join(parts), sku=sku, level="info")

    def lines(self) -> list[str]:
        return list(self._lines)


def create_collection_trace(
    diagnostics: CollectorDiagnostics,
    *,
    task_id: str = "",
    enabled: bool | None = None,
    log_dir: Path | None = None,
) -> CollectionTrace | None:
    from collectors.settings import settings

    if enabled is None:
        enabled = settings.collection_trace_enabled
    if not enabled:
        return None
    base = log_dir or settings.collection_trace_dir
    suffix = f"_{task_id}" if task_id else ""
    log_path = base / f"collection_trace{suffix}.log"
    return CollectionTrace(diagnostics=diagnostics, log_path=log_path, task_id=task_id)
