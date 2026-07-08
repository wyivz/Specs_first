from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DiscoverRequest(BaseModel):
    query: str = ""
    category: str = "Product"
    mode: str = "mock"
    source_urls: list[str] = Field(default_factory=list)


class TaskCreateRequest(BaseModel):
    query: str = ""
    category: str = "Product"
    selected_skus: list[str] | None = None
    source_urls: list[str] = Field(default_factory=list)
    mode: str = "mock"
    vault_path: str = "vault_output"
    discover_only: bool = False
    use_browser: bool = False


class ResumeAuthRequest(BaseModel):
    use_browser: bool = True


class BrowserCommandRequest(BaseModel):
    action: str
    x: int | None = None
    y: int | None = None
    text: str | None = None
    key: str | None = None
    delta: int | None = None


class AsrTranscribeRequest(BaseModel):
    url: str
    language: str = "auto"
    output_dir: str = "vault_output/asr_cache"


class HealthResponse(BaseModel):
    status: str
    service: str


class TaskCreateResponse(BaseModel):
    task_id: str
    state: str


class TaskStatusResponse(BaseModel):
    task_id: str
    state: str
    error: str = ""


class EventsSnapshotResponse(BaseModel):
    task_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)
