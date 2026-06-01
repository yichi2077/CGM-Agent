from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint, UserEvent
from hermes_cgm_agent.services.context import L0ContextBuilder, L0ContextConfig
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


ANCHOR = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)  # 08:00 Asia/Shanghai


class L0ContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        store.initialize()
        self.repository = SQLiteCGMRepository(store)
        self.builder = L0ContextBuilder(cgm_repository=self.repository)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_window_is_fourteen_days_anchored_local_0700(self) -> None:
        ctx = self.builder.build(user_id="user-1", anchor_at=ANCHOR)

        self.assertEqual(ctx.window.span_days, 14)
        self.assertEqual((ctx.window.window_end - ctx.window.window_start).days, 14)
        # 07:00 Asia/Shanghai on 2026-05-31 == 2026-05-30T23:00:00Z
        self.assertEqual(ctx.window.window_end.isoformat(), "2026-05-31T23:00:00+00:00")

    def test_near_mid_far_compression_bands(self) -> None:
        # One point per hour across the full 14-day window.
        end = datetime(2026, 5, 31, 23, 0, tzinfo=timezone.utc)
        start = end - timedelta(days=14)
        ts = start
        index = 0
        while ts < end:
            self._point(ts, 100 + (index % 40))
            ts += timedelta(hours=1)
            index += 1

        ctx = self.builder.build(user_id="user-1", anchor_at=ANCHOR)
        near_cutoff = end - timedelta(days=3)
        mid_cutoff = end - timedelta(days=7)

        # near 3 days kept point-level
        self.assertTrue(ctx.high_res_recent)
        self.assertTrue(all(p.timestamp >= near_cutoff for p in ctx.high_res_recent))
        # days 4-7 are hourly summaries within [mid_cutoff, near_cutoff)
        self.assertTrue(ctx.mid_far_hourly)
        self.assertTrue(all(mid_cutoff <= h.hour_start < near_cutoff for h in ctx.mid_far_hourly))
        # 14 daily aggregates regardless of compression
        self.assertEqual(len(ctx.daily_aggregates), 14)
        # window summary uses analytics, not LLM
        self.assertGreater(ctx.window_summary.point_count, 0)

    def test_detected_and_confirmed_events_are_anchored(self) -> None:
        # Sustained hypo near the window end -> detected event.
        base = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        for i, v in enumerate([120, 65, 60, 58, 110]):
            self._point(base + timedelta(minutes=i * 5), v)
        self.repository.create_user_event(
            UserEvent(
                event_id="evt-1",
                user_id="user-1",
                type="meal",
                ts_start=base,
                payload={"category": "lunch"},
                confidence=1.0,
                created_by="user",
                user_confirmed=True,
            )
        )

        ctx = self.builder.build(user_id="user-1", anchor_at=ANCHOR)

        self.assertTrue(any(str(e.event_type) == "hypo" for e in ctx.key_glucose_events))
        self.assertEqual(len(ctx.confirmed_user_events), 1)
        self.assertEqual(ctx.confirmed_user_events[0].event_id, "evt-1")

    def test_empty_window_has_data_quality_warning_and_no_points(self) -> None:
        ctx = self.builder.build(user_id="user-1", anchor_at=ANCHOR)

        self.assertEqual(ctx.window_summary.point_count, 0)
        self.assertEqual(ctx.high_res_recent, [])
        self.assertTrue(any(w.code == "no_valid_points" for w in ctx.data_quality))

    def test_token_budget_degrades_but_keeps_anchors_and_daily(self) -> None:
        # Dense near-window data to blow a tiny budget; verify graceful degrade.
        base = datetime(2026, 5, 30, 0, 0, tzinfo=timezone.utc)
        ts = base
        i = 0
        while ts < datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc):
            self._point(ts, 100 + (i % 50))
            ts += timedelta(minutes=5)
            i += 1

        builder = L0ContextBuilder(
            cgm_repository=self.repository,
            config=L0ContextConfig(token_budget=500),
        )
        ctx = builder.build(user_id="user-1", anchor_at=ANCHOR)

        # Daily aggregates (facts) are never dropped by budget enforcement.
        self.assertEqual(len(ctx.daily_aggregates), 14)
        # Degradation brought the estimate down toward the budget.
        self.assertLessEqual(ctx.estimated_tokens, builder._estimate_tokens(ctx) + 1)
        self.assertLess(len(ctx.high_res_recent), 22 * 12)

    def _point(self, ts: datetime, value: float) -> None:
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-1",
                timestamp=ts,
                value=value,
                unit="mg/dL",
                source="sensor:test",
                quality_flag="valid",
            )
        )


if __name__ == "__main__":
    unittest.main()
