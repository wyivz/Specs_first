from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.platform_health import build_platform_health


def _task_manager():
    from backend.task_runner import task_manager

    return task_manager


class SpecsFirstApiClient:
    """In-process facade for the Streamlit UI — no FastAPI/TestClient import.

    Importing ``backend.api`` / Starlette TestClient costs multiple seconds on
    cold start; the GUI only needs the shared ``task_manager`` singleton.
    """

    def health(self) -> dict[str, Any]:
        report = build_platform_health(probe_gemini=False)
        return {
            "status": report.overall,
            "service": "specs-first",
            "checked_at": report.checked_at,
            "checks": [asdict(item) for item in report.checks],
        }

    def discover(
        self,
        query: str,
        category: str = "Product",
        mode: str = "mock",
        source_urls: list[str] | None = None,
        *,
        quick: bool = False,
        on_progress: Any | None = None,
    ) -> list[dict[str, Any]]:
        return _task_manager().discover(
            query=query,
            category=category,
            mode=mode,
            source_urls=source_urls,
            quick=quick,
            on_progress=on_progress,
        )

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
        return _task_manager().start_task(
            query=query,
            category=category,
            selected_skus=selected_skus,
            source_urls=source_urls,
            mode=mode,
            vault_path=vault_path,
            use_browser=use_browser,
        )

    def get_task(self, task_id: str) -> dict[str, Any]:
        from frontend.live_data import get_task_status

        return get_task_status(task_id)

    def events_snapshot(self, task_id: str) -> list[dict[str, Any]]:
        from frontend.live_data import events_since

        events, _total = events_since(task_id, 0)
        return events

    def get_result(self, task_id: str) -> dict[str, Any]:
        from frontend.live_data import get_task_result

        return get_task_result(task_id)

    def get_diagnostics(self, task_id: str) -> list[dict[str, Any]]:
        record = _task_manager().get(task_id)
        if not record:
            raise KeyError(f"Task not found: {task_id}")
        if record.result and record.result.diagnostics:
            return list(record.result.diagnostics)
        return []

    def resume_auth(self, task_id: str, use_browser: bool = True) -> None:
        _task_manager().resume_task(task_id, use_browser=use_browser)


_CLIENT: SpecsFirstApiClient | None = None


def get_api_client() -> SpecsFirstApiClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SpecsFirstApiClient()
    return _CLIENT
