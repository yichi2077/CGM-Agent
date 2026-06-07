from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import SQLiteReportRepository
from hermes_cgm_agent.services.tools import ToolExecutor, build_default_tool_registry
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class ReportToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.report_repository = SQLiteReportRepository(self.store)
        self.session_id = "report-tool-test"
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reports_generate_is_active(self) -> None:
        spec = build_default_tool_registry().get("reports.generate")

        self.assertEqual(spec.status, "active")

    def test_reports_generate_returns_report_evidence_and_audit(self) -> None:
        self._create_points([90, 120, 185, 100])

        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "daily",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()
        saved = self.report_repository.get_report(body["report_id"])

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["report"]["report_type"], "daily")
        self.assertIn("血糖日报", body["rendered_markdown"])
        self.assertTrue(body["evidence_refs"])
        self.assertEqual(saved.audit_id, body["audit_id"])
        self.assertEqual(audit_payload["tool_name"], "reports.generate")
        self.assertEqual(audit_payload["status"], "ok")
        self.assertEqual(audit_payload["report_id"], body["report_id"])
        self.assertEqual(audit_payload["template_version"], "g7-report-template-v1")
        self.assertEqual(audit_payload["output_hash"], body["report"]["output_hash"])
        self.assertEqual(audit_payload["route"], "reports.generate")
        self.assertEqual(audit_payload["safety_result"]["status"], "clear")
        self.assertEqual(audit_payload["section_count"], len(body["sections"]))
        self.assertIn("memory_ingest", body)
        self.assertIn("memory_ingest", audit_payload)

    def test_reports_generate_auto_ingests_memory_candidates(self) -> None:
        self._create_points([190, 195, 200, 205])

        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "weekly",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-07T00:00:00+00:00",
                },
            },
        )
        body = response.to_dict()
        memory = SQLiteMemoryRepository(self.store)

        self.assertEqual(body["status"], "ok")
        self.assertGreater(body["memory_ingest"]["enqueued"], 0)
        self.assertEqual(
            len(memory.list_candidates("user-1")),
            body["memory_ingest"]["enqueued"],
        )

    def test_reports_generate_respects_explicit_auto_ingest_false(self) -> None:
        self._create_points([190, 195, 200, 205])

        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "weekly",
                "auto_ingest_memory": False,
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-07T00:00:00+00:00",
                },
            },
        )
        body = response.to_dict()
        memory = SQLiteMemoryRepository(self.store)

        self.assertEqual(body["status"], "ok")
        self.assertGreater(len(body["g8_memory_candidates"]), 0)
        self.assertEqual(body["memory_ingest"]["enabled"], False)
        self.assertEqual(body["memory_ingest"]["enqueued"], 0)
        self.assertEqual(memory.list_candidates("user-1"), [])

    def test_reports_generate_rejects_string_retrieve_context_flag(self) -> None:
        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "daily",
                "retrieve_context": "false",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            },
        ).to_dict()

        self.assertEqual(response["status"], "error")
        self.assertIn("retrieve_context must be a boolean", response["error"])

    def test_doctor_report_does_not_auto_ingest_by_default(self) -> None:
        self._create_points([190, 195, 200, 205])

        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "doctor",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-18T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            },
        )
        body = response.to_dict()
        memory = SQLiteMemoryRepository(self.store)

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["memory_ingest"]["enabled"], False)
        self.assertEqual(memory.list_candidates("user-1"), [])

    def test_reports_generate_validation_error_is_audited(self) -> None:
        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "monthly",
            },
        )
        body = response.to_dict()
        audit_payload = self._last_audit_payload()

        self.assertEqual(body["status"], "error")
        self.assertIn("report_type", body["error"])
        self.assertEqual(audit_payload["tool_name"], "reports.generate")
        self.assertEqual(audit_payload["status"], "error")

    def test_reports_generate_empty_window_returns_quality_warning(self) -> None:
        response = self.executor.execute(
            tool_name="reports.generate",
            session_id=self.session_id,
            arguments={
                "user_id": "user-1",
                "report_type": "daily",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            },
        )
        body = response.to_dict()

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["report"]["data_quality_warnings"][0]["code"], "no_valid_points")

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

    def _create_points(self, values: list[int]) -> None:
        for index, value in enumerate(values):
            self.repository.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, index, 0, tzinfo=timezone.utc),
                    value=value,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )


if __name__ == "__main__":
    unittest.main()
