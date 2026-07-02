from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.domain import DataScope, GlucosePoint
from hermes_cgm_agent.services.analytics import RealtimeSignalService


class RealtimeSignalTests(unittest.TestCase):
    def test_realtime_snapshot_reports_freshness_delta_slope_and_missing_rate(self) -> None:
        base = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        now = base + timedelta(minutes=40)
        points = [
            self._point(base, 100),
            self._point(base + timedelta(minutes=10), 110),
            self._point(base + timedelta(minutes=25), 124),
            self._point(
                base + timedelta(minutes=30),
                130,
                received_at=base + timedelta(minutes=32),
            ),
            self._point(base + timedelta(minutes=30), 999, user_id="other-user"),
        ]
        scope = DataScope(
            user_id="user-1",
            window_start=base,
            window_end=now + timedelta(minutes=1),
            source="sensor:test",
        )

        snapshot = RealtimeSignalService().compute(points=points, scope=scope, now=now)

        self.assertEqual(snapshot.latest_glucose_mg_dl, 130)
        self.assertEqual(snapshot.latest_measured_at, base + timedelta(minutes=30))
        self.assertEqual(snapshot.data_freshness_minutes, 10)
        self.assertEqual(snapshot.collector_lag_minutes, 2)
        self.assertEqual(snapshot.delta_15min, 20)
        self.assertEqual(snapshot.delta_30min, 30)
        self.assertEqual(snapshot.slope_15min_mg_dl_per_min, 1.3333)
        self.assertEqual(snapshot.missing_rate_1h, 66.67)
        self.assertFalse(snapshot.stale_status)
        self.assertEqual(snapshot.point_count_1h, 4)
        self.assertEqual(snapshot.to_dict()["latest_glucose_mg_dl"], 130)

    def test_no_recent_points_returns_stale_empty_snapshot(self) -> None:
        base = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        scope = DataScope(
            user_id="user-1",
            window_start=base,
            window_end=base + timedelta(hours=1),
        )

        snapshot = RealtimeSignalService().compute(
            points=[],
            scope=scope,
            now=base + timedelta(hours=1),
        )

        self.assertIsNone(snapshot.latest_glucose_mg_dl)
        self.assertTrue(snapshot.stale_status)
        self.assertEqual(snapshot.missing_rate_1h, 100.0)

    @staticmethod
    def _point(
        timestamp: datetime,
        value: float,
        *,
        user_id: str = "user-1",
        received_at: datetime | None = None,
    ) -> GlucosePoint:
        return GlucosePoint(
            user_id=user_id,
            timestamp=timestamp,
            received_at=received_at,
            value=value,
            unit="mg/dL",
            source="sensor:test",
            quality_flag="valid",
        )


if __name__ == "__main__":
    unittest.main()
