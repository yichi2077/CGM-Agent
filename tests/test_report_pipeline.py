"""F3-B1 / US1: the citation guard is a mandatory, non-bypassable gate in the
report generation pipeline (contract C4).

A report whose *medical-claim narrative* contains a number that is not backed by
the retrieved authoritative cards MUST NOT be delivered: the builder replaces the
output with the persona-aligned "cannot confirm" response and logs a violation
whose audit payload leaks no clinical content (analyze C1/C4/I2).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timezone
from pathlib import Path

from hermes_cgm_agent.domain import DataScope, GlucosePoint
from hermes_cgm_agent.domain.report import Report, ReportAudience, ReportInput, ReportType
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.reports import ReportService, SQLiteReportRepository
from hermes_cgm_agent.services.reports.renderer import CITATION_BLOCK_TEMPLATE, render_markdown
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class CitationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.report_repository = SQLiteReportRepository(self.store)
        self.audit_events: list[tuple[str, dict]] = []
        self.report_service = ReportService(
            cgm_repository=self.cgm_repository,
            report_repository=self.report_repository,
            audit_logger=lambda event_type, payload: self.audit_events.append(
                (event_type, payload)
            ),
        )
        self._create_points()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_points(self) -> None:
        for index, value in enumerate([90, 100, 150, 120]):
            self.cgm_repository.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, index, 0, tzinfo=timezone.utc),
                    value=value,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )

    def _scope(self) -> dict:
        return {
            "user_id": "user-1",
            "window_start": "2026-05-31T00:00:00+00:00",
            "window_end": "2026-06-01T00:00:00+00:00",
        }

    def test_backed_medical_narrative_is_delivered(self) -> None:
        # (a) every number in the narrative is present in a retrieved card → the
        # report is delivered normally (NOT the cannot-confirm template).
        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope=self._scope(),
                medical_narrative="把目标范围内时间维持在 70% 以上更稳一些。",
                authoritative_context={
                    "enabled": True,
                    "documents": [
                        {
                            "title": "TIR 共识",
                            "text": "建议将目标范围内时间维持在 70% 以上。",
                            "verified": False,
                            "evidence_refs": [
                                {"kind": "authoritative_kb", "ref_id": "kb-1", "summary": "TIR"}
                            ],
                        }
                    ],
                },
            )
        )
        self.assertNotEqual(report.rendered_markdown, CITATION_BLOCK_TEMPLATE)
        self.assertNotEqual(report.safety_result.get("status"), "citation_blocked")
        self.assertIn("70", report.rendered_markdown)
        self.assertEqual(self.audit_events, [])

    def test_unbacked_medical_narrative_is_blocked(self) -> None:
        # (b) the narrative cites 88%, absent from every retrieved card → blocked
        # and replaced with the persona "cannot confirm" response.
        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope=self._scope(),
                medical_narrative="把目标范围内时间维持在 88% 以上才安全。",
                authoritative_context={
                    "enabled": True,
                    "documents": [
                        {
                            "title": "TIR 共识",
                            "text": "建议将目标范围内时间维持在 70% 以上。",
                            "verified": False,
                            "evidence_refs": [
                                {"kind": "authoritative_kb", "ref_id": "kb-1", "summary": "TIR"}
                            ],
                        }
                    ],
                },
            )
        )
        self.assertEqual(report.rendered_markdown, CITATION_BLOCK_TEMPLATE)
        self.assertEqual(report.safety_result.get("status"), "citation_blocked")

    def test_block_logs_violation_without_leaking_content(self) -> None:
        # (c) a violation is audited, but the payload carries NO claim text, NO
        # glucose values, and NO generated narrative (SEC-003 / FR-013).
        self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope=self._scope(),
                medical_narrative="维持在 88% 以上。",
                authoritative_context={
                    "enabled": True,
                    "documents": [
                        {"title": "T", "text": "目标范围内时间。", "verified": False}
                    ],
                },
            )
        )
        self.assertEqual(len(self.audit_events), 1)
        event_type, payload = self.audit_events[0]
        self.assertEqual(event_type, "citation_guard_blocked")
        self.assertEqual(payload.get("violation_count"), 1)
        # No claim text, glucose values, or generated narrative in the payload.
        # (report_id is an opaque uuid and is excluded from the leak check.)
        leak_surface = repr({k: v for k, v in payload.items() if k != "report_id"})
        self.assertNotIn("维持在 88", leak_surface)
        self.assertNotIn("目标范围", leak_surface)
        self.assertNotIn("88%", leak_surface)

    def test_cannot_confirm_response_is_persona_aligned(self) -> None:
        # (d) the block template is gentle, non-directive, offers a data-only
        # alternative (Principle IV).
        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope=self._scope(),
                medical_narrative="维持在 88% 以上。",
                authoritative_context={
                    "enabled": True,
                    "documents": [{"title": "T", "text": "目标范围内。", "verified": False}],
                },
            )
        )
        self.assertIn("无法确认", report.rendered_markdown)
        for banned in ("你应该", "你必须", "你需要", "建议你"):
            self.assertNotIn(banned, report.rendered_markdown)

    def test_clean_report_without_medical_narrative_is_unaffected(self) -> None:
        # The gate only engages when a medical-claim narrative exists; ordinary
        # deterministic reports are never touched (no regression, T008b).
        report = self.report_service.generate(
            ReportInput(report_type="daily", user_id="user-1", data_scope=self._scope())
        )
        self.assertNotEqual(report.rendered_markdown, CITATION_BLOCK_TEMPLATE)
        self.assertEqual(self.audit_events, [])


class RecoveryHeaderRenderTests(unittest.TestCase):
    """F3-B3 / T018b: the renderer surfaces the recovery double-check in the
    report header, and skips it entirely when no recovery window is active."""

    def _report(self, safety_result: dict) -> Report:
        scope = DataScope(
            user_id="u1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )
        return Report(
            report_id="r1",
            user_id="u1",
            report_type=ReportType.DAILY,
            audience=ReportAudience.SELF,
            data_scope=scope,
            timezone="Asia/Shanghai",
            report_anchor_time=time(7, 0),
            safety_result=safety_result,
        )

    def test_recovery_confirmed_renders_header(self) -> None:
        report = self._report(
            {
                "status": "clear",
                "recovery_check": {
                    "active": True,
                    "window_remaining_seconds": 3600,
                    "original": {"status": "red_zone"},
                    "recovery": {"status": "clear"},
                    "recovery_confirmed": True,
                },
            }
        )
        markdown = render_markdown(report)
        self.assertIn("恢复复核", markdown)
        self.assertIn("红区", markdown)
        self.assertIn("是，已回到红区以外", markdown)

    def test_no_recovery_check_renders_no_header(self) -> None:
        report = self._report({"status": "clear"})
        markdown = render_markdown(report)
        self.assertNotIn("恢复复核", markdown)


if __name__ == "__main__":
    unittest.main()
