"""G6: monthly report template (ReportType.MONTHLY + MoM comparison)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint
from hermes_cgm_agent.domain.report import ReportInput, ReportType
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.reports import ReportService, SQLiteReportRepository
from hermes_cgm_agent.services.reports.narrative_templates import (
    render_monthly_comparison,
    render_monthly_summary,
)
from hermes_cgm_agent.services.reports.sections.helpers import (
    REPORT_WINDOW_DAYS,
    _window_label,
    resolve_report_scope,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

ANCHOR = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)


class _Agg:
    """Minimal aggregate stand-in for the template unit tests."""

    def __init__(self, tir=None, tar=None, tbr=None, point_count=100):
        self.tir, self.tar, self.tbr, self.point_count = tir, tar, tbr, point_count


class MonthlyTemplateTests(unittest.TestCase):
    def test_report_type_and_window_wiring(self) -> None:
        self.assertEqual(ReportType.MONTHLY.value, "monthly")
        self.assertEqual(REPORT_WINDOW_DAYS[ReportType.MONTHLY], 30)
        self.assertEqual(_window_label(ReportType.MONTHLY), "month")
        scope = resolve_report_scope(
            user_id="user-1", report_type="monthly", anchor_at=ANCHOR
        )
        self.assertEqual((scope.window_end - scope.window_start).days, 30)

    def test_summary_improvement_narrated_for_self(self) -> None:
        text = render_monthly_summary(
            _Agg(tir=80.0, tar=15.0, tbr=5.0), _Agg(tir=70.0, tar=25.0, tbr=5.0), "self"
        )
        self.assertIn("这个月", text)
        self.assertIn("上个月", text)
        self.assertIn("多了一些", text)

    def test_summary_clinician_keeps_metric_names(self) -> None:
        text = render_monthly_summary(
            _Agg(tir=80.0, tar=15.0, tbr=5.0), _Agg(tir=70.0, tar=25.0, tbr=5.0), "clinician"
        )
        self.assertIn("TIR 80.0%", text)
        self.assertIn("+10.0", text)

    def test_comparison_without_previous_month(self) -> None:
        text = render_monthly_comparison(_Agg(tir=80.0), None, "self")
        self.assertIn("上个月还没有足够的记录", text)

    def test_comparison_flags_more_low_time(self) -> None:
        text = render_monthly_comparison(
            _Agg(tir=70.0, tar=20.0, tbr=10.0), _Agg(tir=78.0, tar=20.0, tbr=2.0), "self"
        )
        self.assertIn("偏低的时间比上个月多了一点", text)


class MonthlyReportPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.report_service = ReportService(
            cgm_repository=self.cgm_repository,
            report_repository=SQLiteReportRepository(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed(self, start: datetime, days: int, values: list[float]) -> None:
        for day in range(days):
            for i, value in enumerate(values):
                self.cgm_repository.create_glucose_point(
                    GlucosePoint(
                        user_id="user-1",
                        timestamp=start + timedelta(days=day, hours=i * 4),
                        value=value,
                        unit="mg/dL",
                        source="sensor:test",
                        quality_flag="valid",
                    )
                )

    def _generate(self):
        return self.report_service.generate(
            ReportInput(
                report_type="monthly",
                user_id="user-1",
                anchor_at=ANCHOR,
            )
        )

    def test_monthly_report_generates_with_expected_sections(self) -> None:
        self._seed(ANCHOR - timedelta(days=30), 30, [95, 120, 150, 110])
        report = self._generate()
        section_ids = [section.section_id for section in report.sections]
        self.assertIn("monthly_summary", section_ids)
        self.assertIn("monthly_patterns", section_ids)
        self.assertIn("metrics", section_ids)
        self.assertEqual(str(report.report_type), "monthly")
        self.assertTrue(report.rendered_markdown)

    def test_monthly_narrative_includes_mom_comparison(self) -> None:
        # Previous month: high burden; this month: in range → MoM improvement.
        self._seed(ANCHOR - timedelta(days=60), 30, [200, 220, 240, 210])
        self._seed(ANCHOR - timedelta(days=30), 30, [95, 120, 150, 110])
        report = self._generate()
        summary = next(
            section for section in report.sections
            if section.section_id == "monthly_summary"
        )
        self.assertIn("上个月", summary.content)

    def test_monthly_report_without_data_does_not_crash(self) -> None:
        report = self._generate()
        self.assertEqual(str(report.report_type), "monthly")


if __name__ == "__main__":
    unittest.main()
