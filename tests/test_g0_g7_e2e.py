from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef, HypothesisState, L3Hypothesis
from hermes_cgm_agent.cli import _import_cgm, _tool_call
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import SQLiteReportRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class G0G7E2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "demo.db"
        self.examples = Path(__file__).resolve().parents[1] / "examples" / "g0_g7_demo"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_demo_chain_imports_events_reports_and_audits(self) -> None:
        import_code, import_payload = self._capture_import()

        self.assertEqual(import_code, 0)
        self.assertEqual(import_payload["status"], "ok")
        self.assertGreater(import_payload["inserted_point_count"], 0)

        aggregate = self._tool("timeseries.get_aggregate", "aggregate_daily.json")
        self.assertEqual(aggregate["status"], "ok")
        self.assertTrue(aggregate["evidence_refs"])

        self.assertEqual(self._tool("events.create", "event_breakfast_confirmed.json")["status"], "ok")
        self.assertEqual(self._tool("events.create", "event_walk_candidate.json")["status"], "ok")
        confirmed = self._tool("events.confirm", "event_walk_confirm.json")
        self.assertEqual(confirmed["status"], "ok")
        self.assertTrue(confirmed["event"]["user_confirmed"])
        self.assertEqual(self._tool("events.create", "event_note_candidate.json")["status"], "ok")
        rejected = self._tool("events.confirm", "event_note_reject.json")
        self.assertEqual(rejected["status"], "ok")
        self.assertTrue(rejected["event"]["is_rejected"])
        self.assertEqual(self._tool("events.create", "event_meal_candidate.json")["status"], "ok")

        report_body = self._tool("reports.generate", "report_daily.json")
        store = SQLiteStore(self.db_path)
        store.initialize()
        repository = SQLiteCGMRepository(store)
        report_repository = SQLiteReportRepository(store)
        status = repository.status()
        saved_report = report_repository.get_report(report_body["report_id"])
        audit_payload = self._audit_payload(store, report_body["audit_id"])

        self.assertEqual(report_body["status"], "ok")
        self.assertGreater(status.glucose_point_count, 0)
        self.assertGreater(status.import_batch_count, 0)
        self.assertGreater(status.user_event_count, 0)
        self.assertEqual(saved_report.report_id, report_body["report_id"])
        self.assertEqual(saved_report.template_version, "g7-report-template-v1")
        self.assertTrue(saved_report.output_hash)
        self.assertEqual(audit_payload["tool_name"], "reports.generate")
        self.assertEqual(audit_payload["report_id"], report_body["report_id"])
        self.assertEqual(audit_payload["output_hash"], saved_report.output_hash)
        key_events = next(
            section
            for section in report_body["sections"]
            if section["section_id"] == "key_events"
        )
        self.assertIn("Confirmed events: 2", key_events["content"])
        self.assertIn("Unconfirmed candidate events: 1", key_events["content"])

        # Pre-G7 hardening increment must survive the full import -> report chain:
        # LBGI/HBGI are computed and the deterministic detected_events section exists.
        section_ids = [section["section_id"] for section in report_body["sections"]]
        self.assertIn("detected_events", section_ids)
        self.assertEqual(saved_report.source_versions["event_detector"], "g6-detector-v1")
        self.assertEqual(saved_report.source_versions["analytics"], "g7-analytics-v2")
        daily_aggregate = next(
            ref for ref in saved_report.evidence_refs if ref.kind == "aggregate"
        )
        self.assertTrue(daily_aggregate.ref_id)

    def test_hypothesis_update_tool_call_updates_existing_record(self) -> None:
        self._capture_import()
        store = SQLiteStore(self.db_path)
        store.initialize()
        memory = SQLiteMemoryRepository(store)
        memory.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id="hyp-demo",
                user_id="demo-user",
                statement="Late dinner tends to raise overnight baseline",
                state=HypothesisState.CANDIDATE,
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev-seed")],
                created_at=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            )
        )

        body = self._tool_payload(
            "hypothesis.update",
            {
                "user_id": "demo-user",
                "hypothesis_id": "hyp-demo",
                "state": "observing",
                "evidence_refs": [{"kind": "aggregate", "ref_id": "agg-demo"}],
            },
        )
        audit_payload = self._latest_audit_payload(store)
        updated = {h.hypothesis_id: h for h in memory.list_hypotheses("demo-user")}["hyp-demo"]

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["hypothesis_id"], "hyp-demo")
        self.assertEqual(body["state"], "observing")
        self.assertEqual(updated.state, HypothesisState.OBSERVING)
        self.assertEqual(audit_payload["tool_name"], "hypothesis.update")
        self.assertEqual(audit_payload["status"], "ok")
        self.assertEqual(audit_payload["state"], "observing")

    def _capture_import(self) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = _import_cgm(
                db_path=self.db_path,
                file_path=self.examples / "cgm_14d.csv",
                source_format="csv",
                user_id="demo-user",
                timezone_name="Asia/Shanghai",
                source=None,
            )
        return code, json.loads(output.getvalue())

    def _tool(self, tool_name: str, input_name: str) -> dict[str, object]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _tool_call(
                db_path=self.db_path,
                tool_name=tool_name,
                input_path=self.examples / input_name,
                session_id="demo-session",
            )
        return json.loads(output.getvalue())

    def _tool_payload(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        temp_path = Path(self.temp_dir.name) / f"{tool_name.replace('.', '_')}.json"
        temp_path.write_text(json.dumps(payload), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _tool_call(
                db_path=self.db_path,
                tool_name=tool_name,
                input_path=temp_path,
                session_id="demo-session",
            )
        return json.loads(output.getvalue())

    def _audit_payload(self, store: SQLiteStore, audit_id: str) -> dict[str, object]:
        with store.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM audit_logs
                WHERE id = ?
                """,
                (audit_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return store.unseal(row["payload_json"], legacy="json")

    def _latest_audit_payload(self, store: SQLiteStore) -> dict[str, object]:
        with store.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM audit_logs
                ORDER BY rowid DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        return store.unseal(row["payload_json"], legacy="json")


if __name__ == "__main__":
    unittest.main()
