from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.domain import DataScope, GlucoseEventSeverity, GlucoseEventType, GlucosePoint
from hermes_cgm_agent.services.analytics import EventDetectionConfig, GlucoseEventDetector


class GlucoseEventDetectionTests(unittest.TestCase):
    def test_detects_sustained_hypo_episode_with_alert_severity(self) -> None:
        # Five consecutive points below 70, dipping to 52 (<= 54 alert threshold).
        values = [120, 65, 60, 52, 58, 110]
        points = _series(values, start_hour=12)
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)
        hypos = [e for e in events if e.event_type == GlucoseEventType.HYPO]

        self.assertEqual(len(hypos), 1)
        self.assertEqual(hypos[0].nadir_value_mg_dl, 52)
        self.assertEqual(hypos[0].severity, GlucoseEventSeverity.ALERT)
        self.assertEqual(hypos[0].point_count, 4)
        self.assertTrue(hypos[0].evidence_refs)

    def test_detects_hyper_episode(self) -> None:
        values = [150, 200, 220, 210, 160]
        points = _series(values, start_hour=12)
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)
        hypers = [e for e in events if e.event_type == GlucoseEventType.HYPER]

        self.assertEqual(len(hypers), 1)
        self.assertEqual(hypers[0].peak_value_mg_dl, 220)
        self.assertEqual(hypers[0].severity, GlucoseEventSeverity.WARNING)

    def test_overnight_low_is_tagged_when_in_night_hours(self) -> None:
        # 18:00 UTC == 02:00 local Asia/Shanghai (UTC+8), i.e. overnight.
        values = [80, 60, 55, 58, 90]
        points = _series(values, start_hour=18)
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)
        types = {e.event_type for e in events}

        self.assertIn(GlucoseEventType.OVERNIGHT_LOW, types)
        self.assertNotIn(GlucoseEventType.HYPO, types)

    def test_detects_rapid_rise_and_fall(self) -> None:
        # +40 mg/dL over 10 min = 4 mg/dL/min (rise), then -40 over 10 min (fall).
        base = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        raw = [(0, 100), (5, 140), (10, 180), (15, 140), (20, 100)]
        points = [_point_at(base + timedelta(minutes=m), v) for m, v in raw]
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)
        types = {e.event_type for e in events}

        self.assertIn(GlucoseEventType.RAPID_RISE, types)
        self.assertIn(GlucoseEventType.RAPID_FALL, types)

    def test_detects_data_gap(self) -> None:
        base = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        # 45-minute gap between two valid points (> 5 * 4 = 20 min).
        points = [
            _point_at(base, 100),
            _point_at(base + timedelta(minutes=45), 110),
        ]
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)
        gaps = [e for e in events if e.event_type == GlucoseEventType.DATA_GAP]

        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].duration_minutes, 45.0)

    def test_no_events_for_stable_in_range_series(self) -> None:
        values = [100, 105, 110, 108, 102, 106]
        points = _series(values, start_hour=12)
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)

        self.assertEqual(events, [])

    def test_event_ids_are_deterministic(self) -> None:
        values = [120, 65, 60, 52, 58, 110]
        points = _series(values, start_hour=12)
        scope = _scope(points)
        detector = GlucoseEventDetector()

        first = detector.detect(points=points, scope=scope)
        second = detector.detect(points=points, scope=scope)

        self.assertEqual([e.event_id for e in first], [e.event_id for e in second])

    def test_ignores_non_valid_quality_points(self) -> None:
        # A warmup point reading low must not create a hypo episode.
        points = _series([120, 110], start_hour=12)
        points.append(_point_at(points[-1].timestamp + timedelta(minutes=5), 50, quality_flag="warmup"))
        scope = _scope(points)

        events = GlucoseEventDetector().detect(points=points, scope=scope)

        self.assertEqual([e for e in events if e.event_type == GlucoseEventType.HYPO], [])


def _series(values: list[float], *, start_hour: int) -> list[GlucosePoint]:
    base = datetime(2026, 5, 31, start_hour, 0, tzinfo=timezone.utc)
    return [_point_at(base + timedelta(minutes=i * 5), v) for i, v in enumerate(values)]


def _point_at(ts: datetime, value: float, *, quality_flag: str = "valid") -> GlucosePoint:
    return GlucosePoint(
        user_id="user-1",
        timestamp=ts,
        value=value,
        unit="mg/dL",
        source="sensor:a",
        quality_flag=quality_flag,
    )


def _scope(points: list[GlucosePoint]) -> DataScope:
    return DataScope(
        user_id="user-1",
        window_start=points[0].timestamp,
        window_end=points[-1].timestamp + timedelta(minutes=5),
    )


if __name__ == "__main__":
    unittest.main()
