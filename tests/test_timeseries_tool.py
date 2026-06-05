from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef, GlucosePoint, HypothesisState, L3Hypothesis
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class TimeseriesToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.session_id = "tool-test"
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
            session_id=self.session_id,
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
            session_id=self.session_id,
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
            session_id=self.session_id,
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

    def test_hypothesis_update_returns_state_and_audit(self) -> None:
        memory = SQLiteMemoryRepository(self.store)
        memory.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id="h1",
                user_id="user-1",
                statement="Breakfast spikes after oatmeal",
                state=HypothesisState.CANDIDATE,
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev-1")],
            )
        )

        response = self.executor.execute(
            tool_name="hypothesis.update",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "hypothesis_id": "h1",
                "state": "observing",
                "evidence_refs": [
                    {"kind": "aggregate", "ref_id": "agg-1", "summary": "Daily aggregate"}
                ],
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()

        saved = {h.hypothesis_id: h for h in memory.list_hypotheses("user-1")}["h1"]

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["hypothesis_id"], "h1")
        self.assertEqual(body["state"], "observing")
        self.assertEqual(saved.state, HypothesisState.OBSERVING)
        self.assertGreaterEqual(saved.evidence_count, 2)
        self.assertEqual(audit_payload["tool_name"], "hypothesis.update")
        self.assertEqual(audit_payload["status"], "ok")
        self.assertEqual(audit_payload["state"], "observing")

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
