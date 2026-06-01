from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from hermes_cgm_agent.api.app import create_app
from hermes_cgm_agent.config import AppConfig
from hermes_cgm_agent.platform.local import LocalAgentPlatform
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        config = AppConfig(db_path=str(db_path))
        store = SQLiteStore(config.database_path)
        self.client = TestClient(
            create_app(
                config=config,
                platform=LocalAgentPlatform(),
                store=store,
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_status_endpoint(self) -> None:
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["project"], "hermes-cgm-agent")
        self.assertTrue(body["hermes_available"])

    def test_chat_creates_session_and_persists_messages(self) -> None:
        response = self.client.post("/chat", json={"prompt": "hello"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        session_id = body["session"]["id"]
        self.assertEqual(body["assistant_message"]["content"], "[local-test-platform] hello")

        sessions = self.client.get("/sessions")
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(len(sessions.json()), 1)
        self.assertEqual(sessions.json()[0]["message_count"], 2)

        detail = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.json()
        self.assertEqual(len(detail_body["messages"]), 2)
        self.assertEqual(len(detail_body["ai_outputs"]), 1)

    def test_delete_session(self) -> None:
        created = self.client.post("/sessions", json={"title": "test"})
        session_id = created.json()["id"]
        deleted = self.client.delete(f"/sessions/{session_id}")
        self.assertEqual(deleted.status_code, 200)
        missing = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
