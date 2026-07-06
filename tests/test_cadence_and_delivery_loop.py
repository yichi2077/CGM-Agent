"""D053: cadence plumb-through + push_tick last-mile webhook delivery."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.domain import GlucosePoint, GlucoseUnit, QualityFlag
from hermes_cgm_agent.services.analytics import median_interval_minutes
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import L0ContextBuilder
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class MedianIntervalTests(unittest.TestCase):
    BASE = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)

    def test_one_minute_cadence(self) -> None:
        stamps = [self.BASE + timedelta(minutes=i) for i in range(30)]
        self.assertEqual(median_interval_minutes(stamps), 1)

    def test_five_minute_cadence_with_one_gap(self) -> None:
        stamps = [self.BASE + timedelta(minutes=5 * i) for i in range(20)]
        stamps.append(self.BASE + timedelta(minutes=5 * 19 + 120))  # one outage
        self.assertEqual(median_interval_minutes(stamps), 5)

    def test_too_few_points_falls_back_to_default(self) -> None:
        stamps = [self.BASE + timedelta(minutes=i) for i in range(3)]
        self.assertEqual(median_interval_minutes(stamps), 5)

    def test_clamped_to_bounds(self) -> None:
        dense = [self.BASE + timedelta(seconds=10 * i) for i in range(30)]
        self.assertEqual(median_interval_minutes(dense), 1)
        sparse = [self.BASE + timedelta(hours=i) for i in range(10)]
        self.assertEqual(median_interval_minutes(sparse), 15)


class L0CadenceAdaptiveTests(unittest.TestCase):
    def test_ten_minute_outage_in_dense_feed_is_reported_as_gap(self) -> None:
        # On a 1-minute feed a 10-minute outage IS a gap. The old hardcoded
        # 5-minute default only flagged gaps >20 minutes, so L0 hid it.
        anchor = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            ts = anchor - timedelta(hours=2)
            minute = 0
            while minute <= 120:
                repo.create_glucose_point(
                    GlucosePoint(
                        user_id="u1",
                        timestamp=ts + timedelta(minutes=minute),
                        value=110,
                        unit=GlucoseUnit.MG_DL,
                        source="test",
                        quality_flag=QualityFlag.VALID,
                    )
                )
                minute += 10 if minute == 60 else 1  # one 10-min outage at t+60
            context = L0ContextBuilder(repository=repo).build(
                user_id="u1", anchor_at=anchor
            )
        gap_events = [
            e for e in context.key_glucose_events if str(e.event_type) == "data_gap"
        ]
        self.assertEqual(len(gap_events), 1)
        self.assertAlmostEqual(gap_events[0].duration_minutes, 10, delta=0.1)


class PushTickDeliveryLoopTests(unittest.TestCase):
    def _executor_with_pushable_data(self, tmp: str) -> ToolExecutor:
        from hermes_cgm_agent.domain import MemorySummary
        from hermes_cgm_agent.services.memory import SQLiteMemoryRepository, new_id

        store = SQLiteStore(Path(tmp) / "app.db")
        store.initialize()
        repo = SQLiteCGMRepository(store)
        now = datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc)  # 11:00 local
        for i in range(60):
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="u1",
                    timestamp=now - timedelta(minutes=5 * i),
                    value=250 if i < 10 else 120,
                    unit=GlucoseUnit.MG_DL,
                    source="test",
                    quality_flag=QualityFlag.VALID,
                )
            )
        # Yesterday's digest with a very different TIR trips the daily-trend
        # trigger (threshold 1: |delta TIR| >= 5).
        SQLiteMemoryRepository(store).create_summary(
            MemorySummary(
                summary_id=new_id(),
                user_id="u1",
                period="daily",
                window_start=now - timedelta(days=2),
                window_end=now - timedelta(days=1),
                content="昨日摘要",
                metrics={"tir_pct": 20.0},
                created_at=now - timedelta(days=1),
            )
        )
        return ToolExecutor(repository=repo, audit_service=AuditService(store))

    def _tick(self, executor: ToolExecutor):
        return executor.execute(
            tool_name="scheduling.push_tick",
            arguments={"user_id": "u1", "now": "2026-07-05T03:00:00Z"},
            session_id="s1",
        )

    def test_configured_webhook_delivers_in_same_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._executor_with_pushable_data(tmp)
            env = {k: v for k, v in os.environ.items() if k != "CGM_WEBHOOK_URL"}
            env["CGM_WEBHOOK_URL"] = "https://receiver.example/hooks/cgm"
            with patch.dict(os.environ, env, clear=True):
                with patch("urllib.request.OpenerDirector.open") as mock_open:
                    mock_open.return_value = _FakeResponse(200)
                    response = self._tick(executor)

        self.assertEqual(response.status, "ok")
        self.assertGreaterEqual(len(response.payload["pushed"]), 1)
        deliveries = response.payload["deliveries"]
        self.assertEqual(len(deliveries), len(response.payload["pushed"]))
        for delivery in deliveries:
            self.assertEqual(delivery["delivery_status"], "sent")
            self.assertTrue(delivery["delivery_id"])
        mock_open.assert_called()

    def test_without_webhook_env_no_delivery_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._executor_with_pushable_data(tmp)
            env = {k: v for k, v in os.environ.items() if k != "CGM_WEBHOOK_URL"}
            with patch.dict(os.environ, env, clear=True):
                with patch("urllib.request.OpenerDirector.open") as mock_open:
                    response = self._tick(executor)

        self.assertEqual(response.status, "ok")
        self.assertEqual(response.payload["deliveries"], [])
        mock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
