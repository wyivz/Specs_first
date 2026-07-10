from __future__ import annotations

import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from backend.api import app


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["status"], {"ok", "degraded", "error"})
        self.assertEqual(payload["service"], "specs-first")
        self.assertTrue(payload.get("checked_at"))
        self.assertTrue(payload.get("checks"))

    def test_discover_mock_candidates(self) -> None:
        response = self.client.post("/discover", json={"query": "Zeiss 50mm", "category": "Lens", "mode": "mock"})
        self.assertEqual(response.status_code, 200)
        candidates = response.json()["candidates"]
        self.assertTrue(candidates)
        self.assertIn("sku", candidates[0])

    def test_create_task_and_poll_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = self.client.post(
                "/tasks",
                json={
                    "query": "Zeiss 50mm 镜头",
                    "category": "Lens",
                    "mode": "mock",
                    "vault_path": tmp,
                },
            )
            self.assertEqual(response.status_code, 200)
            task_id = response.json()["task_id"]

            deadline = time.time() + 30
            state = "RUNNING"
            while time.time() < deadline and state == "RUNNING":
                status = self.client.get(f"/tasks/{task_id}").json()
                state = status["state"]
                time.sleep(0.2)

            self.assertEqual(state, "DONE")
            result = self.client.get(f"/tasks/{task_id}/result").json()
            self.assertEqual(len(result["matrix"]["rows"]), 3)
            self.assertTrue(result["output_paths"])

    def test_events_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_id = self.client.post(
                "/tasks",
                json={"query": "Zeiss 50mm 镜头", "category": "Lens", "mode": "mock", "vault_path": tmp},
            ).json()["task_id"]

            deadline = time.time() + 30
            while time.time() < deadline:
                status = self.client.get(f"/tasks/{task_id}").json()
                if status["state"] != "RUNNING":
                    break
                time.sleep(0.2)

            snapshot = self.client.get(f"/tasks/{task_id}/events/snapshot").json()
            self.assertEqual(snapshot["task_id"], task_id)
            self.assertTrue(any(event["event_type"] == "task_done" for event in snapshot["events"]))


if __name__ == "__main__":
    unittest.main()
