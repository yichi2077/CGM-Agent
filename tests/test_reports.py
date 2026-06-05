from __future__ import annotations

import tempfile
import unittest
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint, UserEvent
from hermes_cgm_agent.domain.report import ReportAudience, ReportInput
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.reports import (
    ReportService,
    SQLiteReportRepository,
    resolve_report_scope,
)
from hermes_cgm_agent.services.safety.router import RED_ZONE_TEMPLATE
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class ReportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.report_repository = SQLiteReportRepository(self.store)
        self.report_service = ReportService(
            cgm_repository=self.cgm_repository,
            report_repository=self.report_repository,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_daily_window_uses_local_anchor_rolling_24_hours(self) -> None:
        scope = resolve_report_scope(
            user_id="user-1",
            report_type="daily",
            timezone_name="Asia/Shanghai",
            anchor_at=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(scope.window_start.isoformat(), "2026-05-30T23:00:00+00:00")
        self.assertEqual(scope.window_end.isoformat(), "2026-05-31T23:00:00+00:00")

    def test_doctor_window_defaults_to_fourteen_days(self) -> None:
        scope = resolve_report_scope(
            user_id="user-1",
            report_type="doctor",
            timezone_name="Asia/Shanghai",
            anchor_at=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual((scope.window_end - scope.window_start).days, 14)

    def test_daily_report_builds_sections_markdown_and_persists(self) -> None:
        self._create_points()
        self.cgm_repository.create_user_event(
            UserEvent(
                event_id="evt-1",
                user_id="user-1",
                type="meal",
                ts_start=datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc),
                payload={"category": "breakfast"},
                confidence=1.0,
                created_by="user",
                user_confirmed=True,
            )
        )

        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        loaded = self.report_repository.get_report(report.report_id)
        section_ids = [section.section_id for section in report.sections]

        self.assertEqual(report.report_type, "daily")
        self.assertIn("daily_card", section_ids)
        self.assertIn("overview", section_ids)
        self.assertIn("metrics", section_ids)
        self.assertIn("key_events", section_ids)
        self.assertIn("血糖日报", report.rendered_markdown)
        self.assertIn("用户版", report.rendered_markdown)
        self.assertEqual(loaded.report_id, report.report_id)
        self.assertEqual(len(report.g8_memory_candidates), 1)
        self.assertEqual(report.g8_memory_candidates[0].target_layer, "L1")
        self.assertEqual(report.template_version, "g7-report-template-v1")
        self.assertEqual(report.route, "reports.generate")
        self.assertEqual(report.safety_result, {"status": "clear", "reason": "no_red_zone_points"})
        self.assertEqual(report.output_hash, sha256(report.rendered_markdown.encode("utf-8")).hexdigest())
        self.assertEqual(loaded.output_hash, report.output_hash)

    def test_red_zone_report_is_routed_to_safety_template(self) -> None:
        self._create_points(values=[92, 48, 110, 305])

        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )

        self.assertEqual(report.route, "reports.generate.red_zone")
        self.assertEqual(report.safety_result["status"], "red_zone")
        self.assertEqual(report.sections[0].section_id, "safety_red_zone")
        self.assertEqual(report.sections[0].content, RED_ZONE_TEMPLATE)
        self.assertIn(RED_ZONE_TEMPLATE, report.rendered_markdown)

    def test_weekly_report_emits_l3_candidate_but_no_memory_table_write(self) -> None:
        self._create_points(values=[190, 195, 200, 205])

        report = self.report_service.generate(
            ReportInput(
                report_type="weekly",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-07T00:00:00+00:00",
                },
            )
        )

        self.assertIn("patterns", [section.section_id for section in report.sections])
        self.assertTrue(any(candidate.target_layer == "L3" for candidate in report.g8_memory_candidates))
        # G7 report generation emits reviewable candidates but must NOT write
        # rows into the G8 memory tables. The tables may exist (G8 schema), but
        # report generation alone leaves them empty.
        with self.store.connect() as conn:
            memory_row_counts = {
                table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                for table in (
                    "l1_episodes",
                    "l2_profile_items",
                    "l3_hypotheses",
                    "memory_candidates",
                )
            }
        self.assertEqual(memory_row_counts, {
            "l1_episodes": 0,
            "l2_profile_items": 0,
            "l3_hypotheses": 0,
            "memory_candidates": 0,
        })

    def test_doctor_report_includes_appendix_and_fourteen_day_label(self) -> None:
        self._create_points()

        report = self.report_service.generate(
            ReportInput(
                report_type="doctor",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-18T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        aggregate_refs = [ref for ref in report.evidence_refs if ref.kind == "aggregate"]

        self.assertIn("doctor_appendix", [section.section_id for section in report.sections])
        self.assertTrue(any(":14d" in ref.ref_id for ref in aggregate_refs))

    def test_report_accepts_rag_context_without_retrieving_it(self) -> None:
        self._create_points()

        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
                memory_context={
                    "enabled": True,
                    "items": [
                        {
                            "summary": "Prior breakfast pattern",
                            "evidence_refs": [
                                {"kind": "user_memory", "ref_id": "mem-1", "summary": "L1 memory"}
                            ],
                        }
                    ],
                },
                authoritative_context={
                    "enabled": True,
                    "documents": [
                        {
                            "title": "CGM FAQ",
                            "evidence_refs": [
                                {
                                    "kind": "authoritative_kb",
                                    "ref_id": "kb-1",
                                    "summary": "FAQ chunk",
                                }
                            ],
                        }
                    ],
                },
            )
        )
        observations = next(section for section in report.sections if section.section_id == "observations")

        self.assertIn("user_memory", observations.source_tracks)
        self.assertIn("authoritative", observations.source_tracks)
        self.assertIn("mixed", observations.source_tracks)
        self.assertTrue(any(ref.kind == "user_memory" for ref in observations.evidence_refs))
        self.assertTrue(any(ref.kind == "authoritative_kb" for ref in observations.evidence_refs))

    def test_report_includes_detected_glucose_events_section(self) -> None:
        # A sustained hypo episode (four points below 70) should be detected and
        # surfaced as a fact-track detected_events section, distinct from user events.
        from datetime import timedelta

        base = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        for index, value in enumerate([120, 65, 60, 58, 110]):
            self.cgm_repository.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=base + timedelta(minutes=index * 5),
                    value=value,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )

        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        detected = next(
            section for section in report.sections if section.section_id == "detected_events"
        )

        self.assertIn("偏低片段", detected.content)
        self.assertTrue(detected.evidence_refs)
        self.assertEqual(report.source_versions["event_detector"], "g6-detector-v1")
        # detected events are fact-track, never user-confirmed UserEvents
        key_events = next(
            section for section in report.sections if section.section_id == "key_events"
        )
        self.assertIn("还没有记下特别的生活事件", key_events.content)

    def test_daily_report_without_exception_emits_only_stable_card(self) -> None:
        from datetime import timedelta

        base = datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)
        for index, value in enumerate([100, 105, 110, 108]):
            self.cgm_repository.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=base + timedelta(minutes=index * 5),
                    value=value,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )

        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )

        section_ids = [section.section_id for section in report.sections]

        self.assertIn("daily_card", section_ids)
        self.assertEqual(report.route, "reports.generate")
        self.assertEqual(report.safety_result["status"], "clear")

    def test_report_supports_audience_specific_narratives(self) -> None:
        self._create_points(values=[95, 145, 190, 175])

        self_report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        clinician_report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.CLINICIAN,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        family_report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.FAMILY,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )

        self.assertIn("可能", self_report.sections[0].content)
        self.assertIn("mg/dL", clinician_report.rendered_markdown)
        self.assertIn("医生报告", clinician_report.rendered_markdown)
        self.assertIn("家属版", family_report.rendered_markdown)
        self.assertIn("今天", family_report.sections[0].content)

    def test_weekly_patterns_use_negotiated_tone(self) -> None:
        from datetime import timedelta

        base = datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc)
        for offset_day in (0, 1):
            for index, value in enumerate([95, 185, 190, 195, 110]):
                self.cgm_repository.create_glucose_point(
                    GlucosePoint(
                        user_id="user-1",
                        timestamp=base + timedelta(days=offset_day, minutes=index * 5),
                        value=value,
                        unit="mg/dL",
                        source="sensor:test",
                        quality_flag="valid",
                    )
                )

        report = self.report_service.generate(
            ReportInput(
                report_type="weekly",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-07T00:00:00+00:00",
                },
            )
        )
        patterns = next(section for section in report.sections if section.section_id == "patterns")

        self.assertIn("看起来可能有关，但还不够确定", patterns.content)

    def _create_points(self, values: list[int] | None = None) -> None:
        for index, value in enumerate(values or [90, 100, 150, 190]):
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


if __name__ == "__main__":
    unittest.main()
