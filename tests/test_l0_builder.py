from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint, UserEvent
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import L0BuildConfig, L0ContextBuilder
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class L0ContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_applies_near_hourly_daily_compression(self) -> None:
        self._point("2026-06-14T00:00:00+00:00", 110)
        self._point("2026-06-10T00:00:00+00:00", 150)
        self._point("2026-06-04T00:00:00+00:00", 180)
        self.repository.create_user_event(
            UserEvent(
                event_id="evt-1",
                user_id="user-1",
                type="meal",
                ts_start=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
                payload={"note": "breakfast"},
                created_by="user",
                user_confirmed=True,
            )
        )

        context = L0ContextBuilder(repository=self.repository).build(
            user_id="user-1",
            anchor_at=datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context.window.span_days, 14)
        self.assertEqual(len(context.high_res_recent), 1)
        self.assertEqual(len(context.mid_far_hourly), 1)
        self.assertEqual(len(context.far_daily_only), 1)
        self.assertEqual(len(context.confirmed_user_events), 1)
        self.assertGreater(context.estimated_tokens, 0)
        self.assertLessEqual(context.estimated_tokens, context.token_budget)

    def test_context_get_l0_tool_returns_context_payload(self) -> None:
        self._point("2026-06-14T00:00:00+00:00", 110)
        executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

        body = executor.execute(
            tool_name="context.get_l0",
            arguments={
                "user_id": "user-1",
                "anchor_at": "2026-06-15T00:00:00+00:00",
            },
            session_id="l0-test",
        ).to_dict()

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["context"]["window"]["user_id"], "user-1")
        self.assertTrue(body["evidence_refs"])

    def test_budget_trims_recent_points(self) -> None:
        for hour in range(24):
            ts = datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=hour * 5)
            self._point(ts.isoformat(), 100)

        context = L0ContextBuilder(
            repository=self.repository,
            config=L0BuildConfig(token_budget=200),
        ).build(
            user_id="user-1",
            anchor_at=datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc),
        )

        self.assertLess(len(context.high_res_recent), 24)
        self.assertLessEqual(context.estimated_tokens, context.token_budget)

    def _point(self, ts: str, value: int) -> None:
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-1",
                timestamp=datetime.fromisoformat(ts),
                value=value,
                unit="mg/dL",
                source="sensor:test",
                quality_flag="valid",
            )
        )


if __name__ == "__main__":
    unittest.main()
