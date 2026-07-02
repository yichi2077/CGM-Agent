"""Replay engine tests (D051): the accelerated-playback demo surface.

Uses fixed 2026-06 dates with ``align_end_to_now=False`` for determinism — the
replay clock and the seeded points share the same native time base, so tier
gating is reproducible regardless of when the test runs. One test exercises the
``align_end_to_now=True`` shift explicitly.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.services.replay import ReplayConfig, ReplayService
from hermes_cgm_agent.storage.sqlite import SQLiteStore

# A 10-day span (2026-06-01 .. 2026-06-10) is guaranteed to contain a Monday, so
# the weekly tier fires deterministically. Values oscillate to give analytics a
# non-degenerate TIR.
_HEADER = "timestamp,value,unit,device_id,record_id"


def _write_fixture(path: Path, *, days: int = 10) -> None:
    lines = [_HEADER]
    rid = 0
    for d in range(days):
        day = datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=d)
        for hour in range(0, 24, 2):  # every 2h -> 12 points/day
            value = 110 + (40 if (d + hour) % 3 else -20)  # oscillate 90..150
            ts = (day + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%S")
            lines.append(f"{ts},{value}.0,mg/dL,SENSOR-R,REC-{rid:05d}")
            rid += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _ReplayTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work = Path(self.temp_dir.name)
        self.csv = self.work / "replay.csv"
        self.db = self.work / "replay.db"
        _write_fixture(self.csv)
        self.store = SQLiteStore(self.db)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _config(self, **overrides: object) -> ReplayConfig:
        base = dict(
            dataset=self.csv,
            user_id="demo",
            align_end_to_now=False,
            speed="instant",
        )
        base.update(overrides)
        return ReplayConfig(**base)  # type: ignore[arg-type]

    def _push_event_count(self, *, delivered_only: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM push_events WHERE user_id = 'demo'"
        if delivered_only:
            sql += " AND delivery_id IS NOT NULL"
        with self.store.connect() as conn:
            return int(conn.execute(sql).fetchone()["n"])


class ReplayMechanicsTests(_ReplayTestBase):
    def test_instant_replay_produces_weekly_push(self) -> None:
        report = ReplayService(store=self.store).run(self._config())
        self.assertGreater(report.points_imported, 0)
        self.assertGreaterEqual(report.days_simulated, 10)
        self.assertGreaterEqual(report.total_pushes, 1)
        tiers = {p["tier"] for tick in report.ticks for p in tick["pushed"]}
        self.assertIn("weekly", tiers)  # a 10-day span always crosses a Monday
        # Every companion push message is non-empty and <=100 chars (FR-005).
        for tick in report.ticks:
            for p in tick["pushed"]:
                self.assertTrue(p["content"])
                self.assertLessEqual(len(p["content"]), 100)

    def test_rerun_is_idempotent(self) -> None:
        service = ReplayService(store=self.store)
        service.run(self._config())
        pushes_after_first = self._push_event_count()

        second = service.run(self._config())
        self.assertEqual(second.points_imported, 0)  # all points dedupe
        self.assertEqual(self._push_event_count(), pushes_after_first)  # no new pushes

    def test_days_trim_reduces_simulated_span(self) -> None:
        full = ReplayService(store=self.store).run(self._config())
        # fresh store for the trimmed run
        db2 = self.work / "replay2.db"
        store2 = SQLiteStore(db2)
        store2.initialize()
        trimmed = ReplayService(store=store2).run(self._config(days=3))
        self.assertLess(trimmed.days_simulated, full.days_simulated)


class ReplayDeliveryTests(_ReplayTestBase):
    def test_deliver_backwrites_delivery_id_and_writes_manifest(self) -> None:
        report = ReplayService(store=self.store).run(self._config(deliver=True))
        self.assertGreaterEqual(report.total_pushes, 1)
        # every emitted push got a delivery_id back-written (D052 bridge)
        self.assertEqual(self._push_event_count(delivered_only=True), report.total_pushes)
        # manifests landed on disk
        deliveries = Path(self.store.db_path).resolve().parent / "deliveries"
        manifests = list(deliveries.glob("*.json"))
        self.assertEqual(len(manifests), report.total_pushes)


class ReplayAlignTests(_ReplayTestBase):
    def test_align_end_to_now_shifts_series_near_now(self) -> None:
        ReplayService(store=self.store).run(self._config(align_end_to_now=True))
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) AS m FROM glucose_points WHERE user_id = 'demo'"
            ).fetchone()
        latest = datetime.fromisoformat(row["m"])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        delta = abs(datetime.now(timezone.utc) - latest)
        self.assertLess(delta, timedelta(hours=48))


if __name__ == "__main__":
    unittest.main()
