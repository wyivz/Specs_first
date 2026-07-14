from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from backend.api import app


@dataclass
class SpecsFirstApiClient:
    """In-process HTTP client for the FastAPI app (shared task_manager singleton)."""

    _client: TestClient = field(default_factory=lambda: TestClient(app))

    def health(self) -> dict[str, Any]:
        response = self._client.get("/health")
        response.raise_for_status()
        return response.json()

    def discover(
        self,
        query: str,
        category: str = "Product",
        mode: str = "mock",
        source_urls: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        response = self._client.post(
            "/discover",
            json={
                "query": query,
                "category": category,
                "mode": mode,
                "source_urls": source_urls or [],
            },
        )
        response.raise_for_status()
        return response.json()["candidates"]

    def start_task(
        self,
        query: str,
        category: str = "Product",
        selected_skus: list[str] | None = None,
        source_urls: list[str] | None = None,
        mode: str = "mock",
        vault_path: str = "vault_output",
        use_browser: bool = False,
    ) -> str:
        response = self._client.post(
            "/tasks",
            json={
                "query": query,
                "category": category,
                "selected_skus": selected_skus,
                "source_urls": source_urls or [],
                "mode": mode,
                "vault_path": vault_path,
                "use_browser": use_browser,
            },
        )
        response.raise_for_status()
        return response.json()["task_id"]

    def get_task(self, task_id: str) -> dict[str, Any]:
        response = self._client.get(f"/tasks/{task_id}")
        response.raise_for_status()
        return response.json()

    def events_snapshot(self, task_id: str) -> list[dict[str, Any]]:
        response = self._client.get(f"/tasks/{task_id}/events/snapshot")
        response.raise_for_status()
        return response.json()["events"]

    def get_result(self, task_id: str) -> dict[str, Any]:
        response = self._client.get(f"/tasks/{task_id}/result")
        response.raise_for_status()
        return response.json()

    def get_diagnostics(self, task_id: str) -> list[dict[str, Any]]:
        response = self._client.get(f"/tasks/{task_id}/diagnostics")
        response.raise_for_status()
        return response.json()["records"]

    def resume_auth(self, task_id: str, use_browser: bool = True) -> None:
        response = self._client.post(f"/tasks/{task_id}/resume-auth", json={"use_browser": use_browser})
        response.raise_for_status()


_CLIENT: SpecsFirstApiClient | None = None


def get_api_client() -> SpecsFirstApiClient:
    """Reuse one TestClient across fragment polls (avoids per-tick setup cost)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SpecsFirstApiClient()
    return _CLIENT
