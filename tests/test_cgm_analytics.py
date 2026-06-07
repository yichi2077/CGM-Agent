from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.domain import DataScope, GlucosePoint
from hermes_cgm_agent.services.analytics import AnalyticsConfig, CGMAnalyticsService


class CGMAnalyticsTests(unittest.TestCase):
    def test_fixed_fixture_metrics_are_reproducible(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc),
        )
        values = [60, 65, 90, 100, 110, 120, 130, 140, 150, 160, 190, 200]
        points = [_point(index, value) for index, value in enumerate(values)]

        aggregate = CGMAnalyticsService().compute_aggregate(
            points=points,
            scope=scope,
            window_label="day",
        )

        self.assertEqual(aggregate.point_count, 12)
        self.assertEqual(aggregate.tir, 66.67)
        self.assertEqual(aggregate.tar, 16.67)
        self.assertEqual(aggregate.tbr, 16.67)
        self.assertEqual(aggregate.mbg, 126.25)
        self.assertEqual(aggregate.cv, 33.8)
        self.assertEqual(aggregate.gmi, 6.33)
        self.assertEqual(aggregate.lbgi, 2.18)
        self.assertEqual(aggregate.hbgi, 2.58)
        self.assertEqual(aggregate.mage, 140.0)
        self.assertIsNone(aggregate.modd)
        self.assertIsNone(aggregate.conga1)
        self.assertIsNone(aggregate.conga2)
        self.assertIsNone(aggregate.conga4)
        self.assertEqual(aggregate.data_coverage, 100.0)

    def test_mage_uses_peak_nadir_excursions_above_one_standard_deviation(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc),
        )
        points = [
            _point(index, value)
            for index, value in enumerate([100, 160, 120, 210, 130, 250, 180])
        ]

        aggregate = CGMAnalyticsService().compute_aggregate(points=points, scope=scope)

        self.assertEqual(aggregate.mage, 84.0)

    def test_mage_returns_none_without_countable_excursions(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc),
        )
        points = [_point(index, 100) for index in range(6)]

        aggregate = CGMAnalyticsService().compute_aggregate(points=points, scope=scope)

        self.assertIsNone(aggregate.mage)

    def test_modd_uses_matching_clock_times_on_adjacent_days(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 2, 1, 0, tzinfo=timezone.utc),
        )
        points = [
            _point_at(datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc), 100),
            _point_at(datetime(2026, 5, 31, 0, 5, tzinfo=timezone.utc), 120),
            _point_at(datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc), 130),
            _point_at(datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc), 150),
            _point_at(datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc), 160),
        ]

        aggregate = CGMAnalyticsService().compute_aggregate(points=points, scope=scope)

        self.assertEqual(aggregate.modd, 30.0)

    def test_conga_uses_standard_deviation_of_lagged_differences(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 7, 0, tzinfo=timezone.utc),
        )
        points = [
            _point_at(datetime(2026, 5, 31, hour, 0, tzinfo=timezone.utc), value)
            for hour, value in enumerate([100, 110, 140, 190, 260, 350, 460])
        ]

        aggregate = CGMAnalyticsService().compute_aggregate(points=points, scope=scope)

        self.assertEqual(aggregate.conga1, 34.16)
        self.assertEqual(aggregate.conga2, 56.57)
        self.assertEqual(aggregate.conga4, 65.32)

    def test_lbgi_hbgi_separate_low_and_high_risk(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc),
        )
        lows = [_point(index, value) for index, value in enumerate([45, 50, 55])]
        highs = [_point(index, value) for index, value in enumerate([300, 350, 400])]

        low_aggregate = CGMAnalyticsService().compute_aggregate(points=lows, scope=scope)
        high_aggregate = CGMAnalyticsService().compute_aggregate(points=highs, scope=scope)

        # Severe lows load LBGI and leave HBGI at zero.
        self.assertEqual(low_aggregate.lbgi, 22.91)
        self.assertEqual(low_aggregate.hbgi, 0.0)
        # Severe highs load HBGI and leave LBGI at zero.
        self.assertEqual(high_aggregate.lbgi, 0.0)
        self.assertEqual(high_aggregate.hbgi, 45.52)

    def test_filters_scope_source_and_quality_flags(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 0, 15, tzinfo=timezone.utc),
            source="sensor:a",
        )
        points = [
            _point(0, 100, source="sensor:a", quality_flag="valid"),
            _point(1, 110, source="sensor:a", quality_flag="warmup"),
            _point(2, 120, source="sensor:b", quality_flag="valid"),
            _point(3, 130, user_id="user-2", source="sensor:a", quality_flag="valid"),
        ]

        aggregate = CGMAnalyticsService().compute_aggregate(points=points, scope=scope)

        self.assertEqual(aggregate.point_count, 1)
        self.assertEqual(aggregate.tir, 100.0)
        self.assertEqual(aggregate.data_coverage, 33.33)
        self.assertEqual(aggregate.mbg, 100.0)

    def test_empty_window_returns_zero_coverage_and_no_mean_metrics(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 0, 30, tzinfo=timezone.utc),
        )

        aggregate = CGMAnalyticsService().compute_aggregate(points=[], scope=scope)

        self.assertEqual(aggregate.point_count, 0)
        self.assertEqual(aggregate.tir, 0)
        self.assertEqual(aggregate.tar, 0)
        self.assertEqual(aggregate.tbr, 0)
        self.assertIsNone(aggregate.mbg)
        self.assertIsNone(aggregate.cv)
        self.assertIsNone(aggregate.gmi)
        self.assertIsNone(aggregate.lbgi)
        self.assertIsNone(aggregate.hbgi)
        self.assertIsNone(aggregate.modd)
        self.assertIsNone(aggregate.conga1)
        self.assertIsNone(aggregate.conga2)
        self.assertIsNone(aggregate.conga4)
        self.assertEqual(aggregate.data_coverage, 0.0)

    def test_custom_thresholds_are_supported(self) -> None:
        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 5, 31, 0, 15, tzinfo=timezone.utc),
        )
        points = [_point(0, 80), _point(1, 120), _point(2, 160)]
        service = CGMAnalyticsService(
            AnalyticsConfig(
                low_threshold_mg_dl=90,
                high_threshold_mg_dl=150,
            )
        )

        aggregate = service.compute_aggregate(points=points, scope=scope)

        self.assertEqual(aggregate.tbr, 33.33)
        self.assertEqual(aggregate.tir, 33.33)
        self.assertEqual(aggregate.tar, 33.33)


def _point(
    index: int,
    value: float,
    *,
    user_id: str = "user-1",
    source: str = "sensor:a",
    quality_flag: str = "valid",
) -> GlucosePoint:
    return GlucosePoint(
        user_id=user_id,
        timestamp=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)
        + timedelta(minutes=index * 5),
        value=value,
        unit="mg/dL",
        source=source,
        quality_flag=quality_flag,
    )


def _point_at(timestamp: datetime, value: float) -> GlucosePoint:
    return GlucosePoint(
        user_id="user-1",
        timestamp=timestamp,
        value=value,
        unit="mg/dL",
        source="sensor:a",
        quality_flag="valid",
    )


if __name__ == "__main__":
    unittest.main()
