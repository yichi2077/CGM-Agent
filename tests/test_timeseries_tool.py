from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class TimeseriesToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.session = self.store.create_session(title="tool-test")
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_timeseries_get_points_returns_points_evidence_and_audit(self) -> None:
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-1",
                timestamp=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                value=100,
                unit="mg/dL",
                source="sensor:test",
                quality_flag="valid",
            )
        )
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-1",
                timestamp=datetime(2026, 5, 31, 0, 5, tzinfo=timezone.utc),
                value=105,
                unit="mg/dL",
                source="sensor:test",
                quality_flag="valid",
            )
        )

        response = self.executor.execute(
            tool_name="timeseries.get_points",
            session_id=self.session.id,
            arguments={
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-05-31T00:10:00+00:00",
                    "source": "sensor:test",
                },
                "limit": 1,
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["points"]), 1)
        self.assertEqual(body["points"][0]["value"], 100)
        self.assertEqual(len(body["evidence_refs"]), 1)
        self.assertIsNotNone(body["audit_id"])
        self.assertEqual(audit_payload["tool_name"], "timeseries.get_points")
        self.assertEqual(audit_payload["status"], "ok")
        self.assertEqual(audit_payload["risk_level"], "read")
        self.assertEqual(audit_payload["point_count"], 1)
        self.assertEqual(audit_payload["data_scope"]["user_id"], "user-1")
        self.assertEqual(audit_payload["evidence_refs"][0]["kind"], "glucose_point")

    def test_timeseries_get_points_validation_error_is_audited(self) -> None:
        response = self.executor.execute(
            tool_name="timeseries.get_points",
            session_id=self.session.id,
            arguments={
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:10:00+00:00",
                    "window_end": "2026-05-31T00:00:00+00:00",
                }
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "error")
        self.assertIn("window_end must be after window_start", body["error"])
        self.assertIsNotNone(body["audit_id"])
        self.assertEqual(audit_payload["status"], "error")
        self.assertEqual(audit_payload["tool_name"], "timeseries.get_points")
        self.assertEqual(audit_payload["evidence_refs"], [])

    def test_timeseries_get_aggregate_returns_metrics_evidence_and_audit(self) -> None:
        for index, value in enumerate([60, 90, 100, 190]):
            self.repository.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, 0, index * 5, tzinfo=timezone.utc),
                    value=value,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )

        response = self.executor.execute(
            tool_name="timeseries.get_aggregate",
            session_id=self.session.id,
            arguments={
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-05-31T00:20:00+00:00",
                    "source": "sensor:test",
                },
                "window_label": "day",
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["aggregate"]["TIR"], 50.0)
        self.assertEqual(body["aggregate"]["TAR"], 25.0)
        self.assertEqual(body["aggregate"]["TBR"], 25.0)
        self.assertEqual(body["aggregate"]["point_count"], 4)
        self.assertEqual(body["evidence_refs"][0]["kind"], "aggregate")
        self.assertIsNotNone(body["audit_id"])
        self.assertEqual(audit_payload["tool_name"], "timeseries.get_aggregate")
        self.assertEqual(audit_payload["status"], "ok")
        self.assertEqual(audit_payload["aggregate"]["point_count"], 4)

    def test_inactive_tool_returns_error_and_audit(self) -> None:
        response = self.executor.execute(
            tool_name="hypothesis.update",
            session_id=self.session.id,
            arguments={
                "user_id": "user-1",
                "hypothesis_id": "h1",
                "state": "observing",
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "error")
        self.assertIn("Tool is not active", body["error"])
        self.assertEqual(audit_payload["tool_name"], "hypothesis.update")
        self.assertEqual(audit_payload["status"], "error")

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
                (self.session.id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row["payload_json"])


if __name__ == "__main__":
    unittest.main()
