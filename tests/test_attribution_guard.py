"""Issue #8: attribution-consistency layer for the LLM medical narrative.

The 14-day simulation audit found a 4.6 mg/dL steady-state fluctuation narrated
as a "餐后小高峰" — numbers passed the citation gate, tone passed the companion
guard, yet the causal attribution contradicted the metrics. These tests lock
the new attribution_consistency_check and its builder integration.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint
from hermes_cgm_agent.domain.report import ReportInput
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.reports import ReportService, SQLiteReportRepository
from hermes_cgm_agent.services.reports.attribution_guard import (
    ATTRIBUTION_CORRECTION_NOTE,
    attribution_consistency_check,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

FLAT_AGGREGATE = {"tar": 0.0, "tbr": 0.0, "cv": 4.6}


class AttributionConsistencyCheckTests(unittest.TestCase):
    def test_postprandial_spike_on_flat_trace_flagged(self) -> None:
        # The exact Issue #8 case: stable ~4.6 CV narrated as a meal spike.
        violations = attribution_consistency_check(
            FLAT_AGGREGATE, "今天午后出现了一次餐后小高峰，可能和午餐有关。"
        )
        self.assertEqual(violations, ["attribution:postprandial_spike~tar=0,cv<10"])

    def test_hyperglycemia_claim_with_zero_tar_flagged(self) -> None:
        violations = attribution_consistency_check(
            {"tar": 0.0, "tbr": 2.0, "cv": 30.0}, "这段时间高血糖时段较多。"
        )
        self.assertEqual(violations, ["attribution:hyperglycemia~tar=0"])

    def test_hypoglycemia_claim_with_zero_tbr_flagged(self) -> None:
        violations = attribution_consistency_check(
            {"tar": 10.0, "tbr": 0.0, "cv": 30.0}, "夜间低血糖的情况值得留意。"
        )
        self.assertEqual(violations, ["attribution:hypoglycemia~tbr=0"])

    def test_volatility_claim_on_flat_trace_flagged(self) -> None:
        violations = attribution_consistency_check(
            FLAT_AGGREGATE, "这段时间血糖波动很大。"
        )
        self.assertIn("attribution:volatility~cv<10", violations)

    def test_consistent_attribution_passes(self) -> None:
        violations = attribution_consistency_check(
            {"tar": 25.0, "tbr": 0.0, "cv": 32.0}, "餐后血糖升高的情况比较集中。"
        )
        self.assertEqual(violations, [])

    def test_no_attribution_claims_pass(self) -> None:
        self.assertEqual(
            attribution_consistency_check(FLAT_AGGREGATE, "整体保持平稳。"), []
        )
        self.assertEqual(attribution_consistency_check(None, "餐后小高峰。"), [])
        self.assertEqual(attribution_consistency_check(FLAT_AGGREGATE, ""), [])

    def test_missing_metrics_do_not_flag(self) -> None:
        # Conservative: without metric evidence there is no contradiction.
        violations = attribution_consistency_check({}, "出现了餐后小高峰。")
        self.assertEqual(violations, [])


class AttributionPipelineTests(unittest.TestCase):
    """The builder appends a correction note (and logs) on inconsistency."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
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
        # A flat, fully in-range trace: TAR=0, TBR=0, CV well under 10%.
        for index, value in enumerate([100, 101, 100, 102, 101, 100]):
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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _generate(self, narrative: str):
        return self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
                medical_narrative=narrative,
            )
        )

    def test_inconsistent_attribution_gets_correction_note_and_audit(self) -> None:
        report = self._generate("今天出现了一次餐后小高峰。")
        self.assertIn(ATTRIBUTION_CORRECTION_NOTE, report.rendered_markdown)
        self.assertIn("今天出现了一次餐后小高峰。", report.rendered_markdown)
        event_types = [event for event, _ in self.audit_events]
        self.assertIn("attribution_inconsistency_flagged", event_types)
        payload = dict(self.audit_events)["attribution_inconsistency_flagged"]
        self.assertEqual(
            payload["violations"], ["attribution:postprandial_spike~tar=0,cv<10"]
        )

    def test_consistent_narrative_has_no_note(self) -> None:
        report = self._generate("整体保持平稳，继续现在的节奏就好。")
        self.assertNotIn(ATTRIBUTION_CORRECTION_NOTE, report.rendered_markdown)
        self.assertEqual(self.audit_events, [])


if __name__ == "__main__":
    unittest.main()
