from __future__ import annotations

import unittest
from datetime import datetime, timezone

from hermes_cgm_agent.domain import (
    DataScope,
    GlucoseAggregate,
    GlucoseEvent,
    GlucoseEventSeverity,
    GlucoseEventType,
)
from hermes_cgm_agent.services.reports.retrieval_query import build_authoritative_query


class RetrievalQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = DataScope(
            user_id="u",
            window_start=datetime(2026, 5, 31, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

    def test_hypoglycemia_event_adds_15_15_and_threshold_terms(self) -> None:
        query, population = build_authoritative_query(
            aggregate=self._aggregate(tir=80, tar=0, tbr=5),
            detected_events=[
                GlucoseEvent(
                    event_id="e1",
                    user_id="u",
                    event_type=GlucoseEventType.HYPO,
                    ts_start=self.scope.window_start,
                    ts_end=self.scope.window_start,
                    severity=GlucoseEventSeverity.WARNING,
                    duration_minutes=0,
                    summary="low",
                )
            ],
        )

        self.assertIsNone(population)
        self.assertIn("hypoglycemia", query)
        self.assertIn("低血糖", query)
        self.assertIn("15 克碳水", query)
        self.assertIn("15 g carbohydrate", query)
        self.assertIn("70", query)

    def test_alert_hyperglycemia_adds_ketone_and_dka_terms(self) -> None:
        query, _ = build_authoritative_query(
            aggregate=self._aggregate(tir=40, tar=60, tbr=0),
            detected_events=[
                GlucoseEvent(
                    event_id="e2",
                    user_id="u",
                    event_type=GlucoseEventType.HYPER,
                    ts_start=self.scope.window_start,
                    ts_end=self.scope.window_start,
                    severity=GlucoseEventSeverity.ALERT,
                    duration_minutes=0,
                    summary="high",
                )
            ],
        )

        self.assertIn("ketone", query)
        self.assertIn("酮体", query)
        self.assertIn("250", query)
        self.assertIn("DKA", query)

    def test_metric_thresholds_add_required_terms(self) -> None:
        query, _ = build_authoritative_query(
            aggregate=self._aggregate(tir=60, tar=30, tbr=5, cv=40, coverage=60),
            detected_events=[],
        )

        self.assertIn("变异系数", query)
        self.assertIn("36", query)
        self.assertIn("低于目标范围", query)
        self.assertIn("4", query)
        self.assertIn("高于目标范围", query)
        self.assertIn("180", query)
        self.assertIn("目标范围内时间", query)
        self.assertIn("70", query)
        self.assertIn("CGM 数据质量", query)
        self.assertIn("14 天", query)

    def test_population_is_returned_and_expands_query_terms(self) -> None:
        query, population = build_authoritative_query(
            aggregate=self._aggregate(tir=90, tar=0, tbr=0),
            detected_events=[],
            population="pregnancy-t1d",
        )

        self.assertEqual(population, "pregnancy-t1d")
        self.assertIn("pregnancy", query)
        self.assertIn("3.5", query)

    def test_normal_window_fallback_is_non_empty(self) -> None:
        query, _ = build_authoritative_query(
            aggregate=self._aggregate(tir=90, tar=0, tbr=0),
            detected_events=[],
            report_type="weekly",
        )

        self.assertIn("time in range", query)
        self.assertIn("weekly", query)

    def _aggregate(
        self,
        *,
        tir: float,
        tar: float,
        tbr: float,
        cv: float | None = None,
        coverage: float = 100,
    ) -> GlucoseAggregate:
        return GlucoseAggregate(
            user_id="u",
            window_start=self.scope.window_start,
            window_end=self.scope.window_end,
            window_label="day",
            TIR=tir,
            TAR=tar,
            TBR=tbr,
            CV=cv if cv is not None else (37 if tir < 70 else 20),
            MBG=120,
            data_coverage=coverage,
            point_count=10,
        )


if __name__ == "__main__":
    unittest.main()
