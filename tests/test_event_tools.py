from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class EventToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.session_id = "event-tool-test"
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_events_create_stores_agent_candidate_without_confirming_fact(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "type": "meal",
                    "ts_start": "2026-05-31T08:00:00+00:00",
                    "payload": {"category": "breakfast"},
                    "confidence": 0.8,
                },
            },
        )
        body = response.to_dict()
        event_id = body["event_id"]
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )
        all_events = self.repository.list_user_events(scope)
        confirmed_events = self.repository.list_user_events(scope, confirmed_only=True)
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "ok")
        self.assertTrue(event_id)  # server-generated, not model-supplied
        self.assertEqual(body["event"]["created_by"], "agent")
        self.assertFalse(body["event"]["user_confirmed"])
        self.assertEqual(body["evidence_refs"][0]["kind"], "event")
        self.assertEqual([event.event_id for event in all_events], [event_id])
        self.assertEqual(confirmed_events, [])
        self.assertEqual(audit_payload["tool_name"], "events.create")
        self.assertEqual(audit_payload["status"], "ok")
        self.assertFalse(audit_payload["user_confirmed"])

    def test_events_create_succeeds_with_minimal_fields(self) -> None:
        # FR-006 / C2: only event_type + ts_start are required (event_type via the
        # flattened schema name; populate_by_name accepts it).
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {"event_type": "exercise", "ts_start": "2026-05-31T18:00:00+00:00"},
            },
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["event_id"])
        self.assertEqual(body["event"]["created_by"], "agent")
        self.assertFalse(body["event"]["user_confirmed"])

    def test_events_create_forces_agent_unconfirmed_provenance(self) -> None:
        # FR-007 / Damocles W2: model-supplied id / created_by / user_confirmed are
        # hard-overridden so an agent event can never pose as user-authored/confirmed.
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "type": "meal",
                    "ts_start": "2026-05-31T08:00:00+00:00",
                    "event_id": "hacked",
                    "created_by": "user",
                    "user_confirmed": True,
                },
            },
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertNotEqual(body["event_id"], "hacked")
        self.assertEqual(body["event"]["created_by"], "agent")
        self.assertFalse(body["event"]["user_confirmed"])
        self.assertEqual(body["event"]["user_id"], "user-1")

    def test_events_create_rejects_non_object_event(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={"user_id": "user-1", "event": "not-an-object"},
        )
        self.assertEqual(response.to_dict()["status"], "error")

    def test_events_confirm_promotes_candidate_and_applies_correction(self) -> None:
        event_id = self._create_candidate()

        response = self.executor.execute(
            tool_name="events.confirm",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event_id": event_id,
                "confirmed": True,
                "correction": {
                    "payload": {"category": "lunch"},
                    "confidence": 1.0,
                },
            },
        )
        body = response.to_dict()
        saved = self.repository.get_user_event(event_id)
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["event"]["user_confirmed"])
        self.assertFalse(body["event"]["is_rejected"])
        self.assertEqual(saved.payload["category"], "lunch")
        self.assertEqual(saved.confidence, 1.0)
        self.assertEqual(audit_payload["tool_name"], "events.confirm")
        self.assertTrue(audit_payload["confirmed"])
        self.assertEqual(audit_payload["evidence_refs"][0]["kind"], "event")

    def test_events_confirm_rejects_candidate_and_hides_from_default_queries(self) -> None:
        event_id = self._create_candidate()

        response = self.executor.execute(
            tool_name="events.confirm",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event_id": event_id,
                "confirmed": False,
            },
        )
        body = response.to_dict()
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )
        visible_events = self.repository.list_user_events(scope)
        rejected_events = self.repository.list_user_events(scope, include_rejected=True)
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["event"]["user_confirmed"])
        self.assertTrue(body["event"]["is_rejected"])
        self.assertEqual(visible_events, [])
        self.assertEqual([event.event_id for event in rejected_events], [event_id])
        self.assertFalse(audit_payload["confirmed"])
        self.assertTrue(audit_payload["is_rejected"])

    def test_events_confirm_rejects_cross_user_ownership(self) -> None:
        # C2: a caller must not confirm/mutate another user's event by id alone.
        event_id = self._create_candidate()

        response = self.executor.execute(
            tool_name="events.confirm",
            session_id=self.session_id,
            arguments={
                "user_id": "attacker",
                "event_id": event_id,
                "confirmed": True,
            },
        )
        body = response.to_dict()

        self.assertEqual(body["status"], "error")
        saved = self.repository.get_user_event(event_id, include_rejected=True)
        self.assertFalse(saved.user_confirmed)
        self.assertFalse(saved.is_rejected)

    def test_events_confirm_rejects_non_boolean_confirmed(self) -> None:
        # C3: a string like "false" must not be coerced to True.
        event_id = self._create_candidate()

        response = self.executor.execute(
            tool_name="events.confirm",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event_id": event_id,
                "confirmed": "false",
            },
        )
        body = response.to_dict()

        self.assertEqual(body["status"], "error")
        saved = self.repository.get_user_event(event_id, include_rejected=True)
        self.assertFalse(saved.user_confirmed)
        self.assertFalse(saved.is_rejected)

    def test_events_confirm_rejects_non_object_correction(self) -> None:
        event_id = self._create_candidate()

        response = self.executor.execute(
            tool_name="events.confirm",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event_id": event_id,
                "confirmed": True,
                "correction": "bad",
            },
        )
        body = response.to_dict()

        self.assertEqual(body["status"], "error")
        self.assertIn("correction must be an object", body["error"])
        saved = self.repository.get_user_event(event_id, include_rejected=True)
        self.assertFalse(saved.user_confirmed)
        self.assertFalse(saved.is_rejected)

    def _create_candidate(self) -> str:
        response = self.executor.execute(
            tool_name="events.create",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "event": {
                    "type": "meal",
                    "ts_start": "2026-05-31T08:00:00+00:00",
                    "payload": {"category": "breakfast"},
                },
            },
        )
        self.assertEqual(response.status, "ok")
        return response.to_dict()["event_id"]

    def _last_audit_payload(self) -> dict[str, object]:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM audit_logs
                WHERE session_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (self.session_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return self.store.unseal(row["payload_json"], legacy="json")


if __name__ == "__main__":
    unittest.main()
