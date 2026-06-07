from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import UserEvent
from hermes_cgm_agent.services.data import EventToolService, SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class EventToolServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.service = EventToolService(self.repository)
        self.repository.create_user_event(
            UserEvent(
                event_id="evt-1",
                user_id="user-1",
                type="meal",
                ts_start=datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc),
                payload={"category": "breakfast"},
                created_by="agent",
                user_confirmed=False,
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_confirm_event_promotes_candidate_and_applies_correction(self) -> None:
        result = self.service.confirm_event(
            {
                "user_id": "user-1",
                "event_id": "evt-1",
                "confirmed": True,
                "correction": {"payload": {"category": "lunch"}, "confidence": 1.0},
            }
        )

        self.assertTrue(result.confirmed)
        self.assertTrue(result.event.user_confirmed)
        self.assertFalse(result.event.is_rejected)
        self.assertEqual(result.event.payload["category"], "lunch")
        self.assertEqual(result.event.confidence, 1.0)

    def test_confirm_event_rejects_candidate(self) -> None:
        result = self.service.confirm_event(
            {"user_id": "user-1", "event_id": "evt-1", "confirmed": False}
        )

        self.assertFalse(result.confirmed)
        self.assertFalse(result.event.user_confirmed)
        self.assertTrue(result.event.is_rejected)

    def test_confirm_event_is_user_scoped(self) -> None:
        with self.assertRaisesRegex(KeyError, "evt-1"):
            self.service.confirm_event(
                {"user_id": "other-user", "event_id": "evt-1", "confirmed": True}
            )

        saved = self.repository.get_user_event("evt-1", include_rejected=True)
        self.assertFalse(saved.user_confirmed)
        self.assertFalse(saved.is_rejected)

    def test_confirm_event_rejects_non_boolean_confirmed(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmed must be a boolean"):
            self.service.confirm_event(
                {"user_id": "user-1", "event_id": "evt-1", "confirmed": "false"}
            )

    def test_confirm_event_rejects_non_object_correction(self) -> None:
        with self.assertRaisesRegex(ValueError, "correction must be an object"):
            self.service.confirm_event(
                {
                    "user_id": "user-1",
                    "event_id": "evt-1",
                    "confirmed": True,
                    "correction": "bad",
                }
            )


if __name__ == "__main__":
    unittest.main()
