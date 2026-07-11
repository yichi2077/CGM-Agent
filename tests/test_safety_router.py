"""Tests for the three-zone safety router."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hermes_cgm_agent.domain import DataScope, EvidenceKind, GlucosePoint, GlucoseUnit, QualityFlag
from hermes_cgm_agent.services.safety.router import (
    RECOVERY_WINDOW_SECONDS,
    RED_ZONE_HIGH_MGDL,
    RED_ZONE_LOW_MGDL,
    YELLOW_ZONE_HIGH_MGDL,
    YELLOW_ZONE_LOW_MGDL,
    SafetyRouter,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

UTC = timezone.utc


def _point(
    value: float,
    unit: GlucoseUnit = GlucoseUnit.MG_DL,
    *,
    minutes_offset: float = 0,
) -> GlucosePoint:
    return GlucosePoint(
        user_id="u1",
        timestamp=datetime(2026, 6, 6, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes_offset),
        value=value,
        unit=unit,
        source="test",
        quality_flag=QualityFlag.VALID,
    )


def _sustained_red(
    value: float = 40,
    count: int = 3,
    *,
    unit: GlucoseUnit = GlucoseUnit.MG_DL,
) -> list[GlucosePoint]:
    """Sustained red-zone points spanning >= 10 minutes (passes A2 time gating).

    Three points at 5-minute intervals span exactly 10 minutes.
    """
    return [_point(value, unit=unit, minutes_offset=5 * i) for i in range(count)]


def _scope() -> DataScope:
    return DataScope(
        user_id="u1",
        window_start=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 6, 23, 59, tzinfo=UTC),
    )


class GreenZoneTests(unittest.TestCase):
    def test_normal_values_return_clear(self) -> None:
        points = [_point(100), _point(120), _point(90)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "clear")
        self.assertEqual(decision.route, "reports.generate")
        self.assertIsNone(decision.message)

    def test_boundary_values_still_green(self) -> None:
        """Exactly at yellow thresholds should still be green (strict < / >)."""
        points = [_point(YELLOW_ZONE_LOW_MGDL), _point(YELLOW_ZONE_HIGH_MGDL)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "clear")

    def test_red_boundary_values_are_yellow_not_red(self) -> None:
        """Exactly 54 / 250 mg/dL belong to the YELLOW zone (strict < / >).

        Matches the ADA definitions the thresholds are drawn from: level-2
        hypoglycemia is <54 (54 itself is level-1 territory) and the red high
        cut is >250. Locks the interval semantics so a future refactor cannot
        silently flip a boundary reading into (or out of) the hard red path.
        """
        for boundary in (RED_ZONE_LOW_MGDL, RED_ZONE_HIGH_MGDL):
            decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(boundary)])
            self.assertEqual(
                decision.safety_result["status"], "yellow_zone",
                f"{boundary} mg/dL must be yellow, got {decision.safety_result['status']}",
            )

    def test_empty_points_returns_green(self) -> None:
        decision = SafetyRouter().evaluate(scope=_scope(), points=[])
        self.assertEqual(decision.safety_result["status"], "clear")


class YellowZoneTests(unittest.TestCase):
    def test_low_yellow_detected(self) -> None:
        points = [_point(65)]  # below 70
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertEqual(decision.safety_result["direction"], "偏低")
        self.assertIsNotNone(decision.message)
        self.assertIn("偏低", decision.message)

    def test_high_yellow_detected(self) -> None:
        points = [_point(220)]  # above 200 but below 250
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertEqual(decision.safety_result["direction"], "偏高")

    def test_yellow_uses_mg_dl_not_raw_value(self) -> None:
        """BUG FIX: a mmol/L value of 3.5 (=63 mg/dL) must trigger yellow,
        not be compared raw against mg/dL thresholds."""
        points = [_point(3.5, unit=GlucoseUnit.MMOL_L)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        # 3.5 mmol/L = 63 mg/dL -> below YELLOW_ZONE_LOW (70) -> yellow
        self.assertEqual(decision.safety_result["status"], "yellow_zone")

    def test_yellow_evidence_refs_use_mg_dl(self) -> None:
        points = [_point(65)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertIsNotNone(decision.evidence_refs)
        ref = decision.evidence_refs[0]
        self.assertEqual(ref.kind, EvidenceKind.GLUCOSE_POINT)
        self.assertIn("mg/dL", ref.summary)

    def test_yellow_route_is_reports_generate(self) -> None:
        """Yellow zone still allows report generation (not deferred)."""
        points = [_point(65)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.route, "reports.generate")


class RedZoneTests(unittest.TestCase):
    def test_low_red_detected(self) -> None:
        points = _sustained_red(50)  # below 54, sustained >= 10 min
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertEqual(decision.route, "reports.generate.red_zone")

    def test_high_red_detected(self) -> None:
        points = _sustained_red(260)  # above 250, sustained >= 10 min
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_red_uses_mg_dl_not_raw_value(self) -> None:
        """BUG FIX: a mmol/L value of 2.5 (=45 mg/dL) must trigger red."""
        points = _sustained_red(2.5, unit=GlucoseUnit.MMOL_L)
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_red_takes_precedence_over_yellow(self) -> None:
        """If both red and yellow points exist, red wins."""
        points = _sustained_red(50) + [_point(65, minutes_offset=15)]  # red + yellow
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_red_min_max_in_mg_dl(self) -> None:
        points = [
            _point(40, minutes_offset=0),
            _point(45, minutes_offset=5),
            _point(50, minutes_offset=10),
        ]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        result = decision.safety_result
        self.assertEqual(result["min_value_mgdl"], 40.0)
        self.assertEqual(result["max_value_mgdl"], 50.0)

    def test_red_min_max_with_mmol_input(self) -> None:
        """min/max values must be reported in mg/dL even when input is mmol/L."""
        points = (
            _sustained_red(2.5, unit=GlucoseUnit.MMOL_L)
            + _sustained_red(20.0, unit=GlucoseUnit.MMOL_L, count=0)
        )
        # Build sustained run with both low and high red points.
        points = [
            _point(2.5, unit=GlucoseUnit.MMOL_L, minutes_offset=0),
            _point(2.5, unit=GlucoseUnit.MMOL_L, minutes_offset=5),
            _point(20.0, unit=GlucoseUnit.MMOL_L, minutes_offset=10),
            _point(20.0, unit=GlucoseUnit.MMOL_L, minutes_offset=15),
            _point(20.0, unit=GlucoseUnit.MMOL_L, minutes_offset=20),
        ]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        result = decision.safety_result
        # 2.5 mmol/L ~ 45 mg/dL, 20.0 mmol/L ~ 360.5 mg/dL
        self.assertLess(result["min_value_mgdl"], RED_ZONE_LOW_MGDL)
        self.assertGreater(result["max_value_mgdl"], RED_ZONE_HIGH_MGDL)

    def test_red_message_is_medical_deferral(self) -> None:
        points = _sustained_red(50)
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertIn("医疗判断", decision.message)

    def test_red_evidence_refs_capped_at_5(self) -> None:
        points = [
            _point(v, minutes_offset=i * 2)
            for i, v in enumerate([40, 42, 44, 46, 48, 50, 52])
        ]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(len(decision.evidence_refs), 5)


class TimeGatingTests(unittest.TestCase):
    """A2: transient red anomalies (< 10 min sustained) are downgraded to yellow."""

    def test_single_red_point_is_transient(self) -> None:
        """A single red point (duration 0) is downgraded to yellow."""
        decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(40)])
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertTrue(decision.transient_suppressed)

    def test_two_red_points_5min_apart_is_transient(self) -> None:
        """Two red points 5 minutes apart (duration 5 min < 10) are transient."""
        points = [_point(40, minutes_offset=0), _point(40, minutes_offset=5)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertTrue(decision.transient_suppressed)

    def test_three_red_points_10min_apart_is_sustained(self) -> None:
        """Three red points spanning 10 min (>= threshold) are sustained red."""
        points = _sustained_red(40)  # 0, 5, 10 min -> 10 min span
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertFalse(decision.transient_suppressed)

    def test_transient_red_includes_actual_values(self) -> None:
        """Downgraded yellow still reports the actual out-of-range values."""
        decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(40)])
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertEqual(decision.safety_result["min_value_mgdl"], 40.0)
        self.assertEqual(decision.safety_result["direction"], "偏低")

    def test_sustained_high_red_not_downgraded(self) -> None:
        """High red spanning >= 10 min stays red (not transient)."""
        points = _sustained_red(260)  # above 250, sustained
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertFalse(decision.transient_suppressed)

    def test_red_run_broken_by_green_is_transient(self) -> None:
        """Red points separated by a green point do not form a sustained run."""
        points = [
            _point(40, minutes_offset=0),
            _point(100, minutes_offset=5),
            _point(40, minutes_offset=10),
            _point(40, minutes_offset=15),
            _point(40, minutes_offset=20),
        ]
        # The second run (10-20 min) spans 10 min -> sustained.
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_all_red_runs_short_is_transient(self) -> None:
        """Multiple short red runs (each < 10 min) are all transient."""
        points = [
            _point(40, minutes_offset=0),
            _point(100, minutes_offset=5),
            _point(40, minutes_offset=10),
            _point(100, minutes_offset=15),
        ]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertTrue(decision.transient_suppressed)

    def test_large_data_gap_does_not_prove_sustained_red(self) -> None:
        """Isolated red readings separated by a data gap are not continuous."""
        points = [_point(40, minutes_offset=0), _point(40, minutes_offset=60)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertTrue(decision.transient_suppressed)

    def test_sustained_zone_with_gap_returns_red(self) -> None:
        """C-01: a sustained red run (>=10 min) followed by a data gap and
        more red points must still be detected as red_zone.

        Without the fix, the gap resets the run without checking whether
        the previous run already reached the sustained threshold, causing
        a real ≥10-minute red-zone episode to be silently downgraded to
        yellow (transient_suppressed).
        """
        points = [
            _point(40, minutes_offset=0),
            _point(40, minutes_offset=5),
            _point(40, minutes_offset=10),
            _point(40, minutes_offset=25),  # 15-min gap from t=10
        ]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertFalse(decision.transient_suppressed)


class ThresholdBoundaryTests(unittest.TestCase):
    """A1: verify the corrected threshold scheme (green 70-180, yellow 54-70 /
    180-250, red <54 / >250)."""

    def test_180_is_green_boundary(self) -> None:
        """Exactly 180 mg/dL is green (strict > comparison)."""
        decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(180)])
        self.assertEqual(decision.safety_result["status"], "clear")

    def test_181_is_yellow(self) -> None:
        """181 mg/dL is yellow high."""
        decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(181)])
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertEqual(decision.safety_result["direction"], "偏高")

    def test_250_is_yellow_boundary(self) -> None:
        """Exactly 250 mg/dL is yellow (strict > comparison for red)."""
        decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(250)])
        self.assertEqual(decision.safety_result["status"], "yellow_zone")

    def test_251_sustained_is_red(self) -> None:
        """251 mg/dL sustained >= 10 min is red."""
        points = _sustained_red(251)
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_251_transient_is_yellow(self) -> None:
        """251 mg/dL as a single point (transient) is yellow."""
        decision = SafetyRouter().evaluate(scope=_scope(), points=[_point(251)])
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertTrue(decision.transient_suppressed)


class RecoveryDoubleCheckTests(unittest.TestCase):
    """F3-B3 / US3 (analyze D1): a red-zone event arms a recovery double-check.

    A LATER evaluation within the 2-hour window compares the STORED original red
    result against the CURRENT result -- it never re-evaluates the same data and
    never recurses into ``evaluate()``.
    """

    def setUp(self) -> None:
        self.router = SafetyRouter()
        self.t0 = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

    def test_recovery_confirmed_when_green_after_red_in_window(self) -> None:
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(hours=1)
        )
        self.assertIsNotNone(later.recovery_check)
        self.assertEqual(later.recovery_check["original"]["status"], "red_zone")
        self.assertEqual(later.recovery_check["recovery"]["status"], "clear")
        self.assertTrue(later.recovery_check["recovery_confirmed"])
        self.assertTrue(later.recovery_check["active"])

    def test_no_recovery_after_window_expires_and_state_cleared(self) -> None:
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(),
            points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 1),
        )
        self.assertIsNone(later.recovery_check)
        # State was cleared: a further green eval still has no recovery check.
        again = self.router.evaluate(
            scope=_scope(),
            points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 2),
        )
        self.assertIsNone(again.recovery_check)

    def test_green_without_prior_red_has_no_recovery(self) -> None:
        decision = self.router.evaluate(scope=_scope(), points=[_point(100)], now=self.t0)
        self.assertIsNone(decision.recovery_check)

    def test_recovery_not_confirmed_when_still_red(self) -> None:
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=_sustained_red(40), now=self.t0 + timedelta(hours=1)
        )
        self.assertIsNotNone(later.recovery_check)
        self.assertFalse(later.recovery_check["recovery_confirmed"])

    def test_original_equals_stored_t0_red_not_a_reeval(self) -> None:
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(hours=1)
        )
        self.assertEqual(later.recovery_check["original"]["min_value_mgdl"], 40.0)

    def test_window_boundary_exactly_at_limit_is_expired(self) -> None:
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(),
            points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS),
        )
        self.assertIsNone(later.recovery_check)

    def test_env_override_changes_window(self) -> None:
        with mock.patch.dict(os.environ, {"CGM_AGENT_RECOVERY_WINDOW_SECONDS": "60"}):
            self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
            expired = self.router.evaluate(
                scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(seconds=61)
            )
            self.assertIsNone(expired.recovery_check)
            self.router.evaluate(
                scope=_scope(), points=_sustained_red(40), now=self.t0 + timedelta(minutes=10)
            )
            within = self.router.evaluate(
                scope=_scope(),
                points=[_point(100)],
                now=self.t0 + timedelta(minutes=10, seconds=30),
            )
            self.assertIsNotNone(within.recovery_check)

    def test_evaluation_does_not_recurse(self) -> None:
        # The inner zone re-eval is non-recursive: exactly one red-zone state
        # entry is recorded per user (no runaway / double tracking).
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        self.assertEqual(len(self.router._last_red_zone), 1)

    def test_internal_red_zone_state_is_not_serialized(self) -> None:
        # SEC-004 / T021: the router's private last-red-zone timestamp state must
        # never leak into a serialized SafetyDecision (no raw datetimes, no
        # internal dict name).
        from dataclasses import asdict
        from datetime import datetime as _dt

        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(hours=1)
        )
        blob = asdict(later)
        self.assertNotIn("_last_red_zone", blob)

        def _no_datetime(obj: object) -> bool:
            if isinstance(obj, _dt):
                return False
            if isinstance(obj, dict):
                return all(_no_datetime(v) for v in obj.values())
            if isinstance(obj, (list, tuple)):
                return all(_no_datetime(v) for v in obj)
            return True

        self.assertTrue(_no_datetime(blob))

    def test_recovery_check_has_original_triggered_at(self) -> None:
        """A3: recovery_check exposes original_triggered_at as ISO string."""
        self.router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(hours=1)
        )
        self.assertIsNotNone(later.recovery_check)
        self.assertEqual(
            later.recovery_check["original_triggered_at"],
            self.t0.isoformat(),
        )


class LongRedZoneBaselineTests(unittest.TestCase):
    """A3: when a red zone persists beyond the recovery window, the original
    trigger time and result are preserved (not replaced by the renewal)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "safety.db"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()
        self.t0 = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_long_red_zone_baseline_preserved_in_memory(self) -> None:
        """In-memory: renewal preserves original_triggered_at and result."""
        router = SafetyRouter()
        router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        # Window expired, still red -> renew
        router.evaluate(
            scope=_scope(), points=_sustained_red(40),
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 1),
        )
        # Green after renewal -> recovery check references original
        result = router.evaluate(
            scope=_scope(), points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 60),
        )
        self.assertIsNotNone(result.recovery_check)
        self.assertTrue(result.recovery_check["recovery_confirmed"])
        self.assertEqual(result.recovery_check["original"]["status"], "red_zone")
        self.assertEqual(result.recovery_check["original"]["min_value_mgdl"], 40.0)
        self.assertEqual(
            result.recovery_check["original_triggered_at"],
            self.t0.isoformat(),
        )

    def test_long_red_zone_baseline_preserved_in_sqlite(self) -> None:
        """SQLite: renewal preserves original_triggered_at and result."""
        router = SafetyRouter(store=self.store)
        router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        # Window expired, still red -> renew (triggered_at updated, original kept)
        router.evaluate(
            scope=_scope(), points=_sustained_red(40),
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 1),
        )
        # Green after renewal -> recovery check references original
        result = router.evaluate(
            scope=_scope(), points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 60),
        )
        self.assertIsNotNone(result.recovery_check)
        self.assertTrue(result.recovery_check["recovery_confirmed"])
        self.assertEqual(result.recovery_check["original"]["min_value_mgdl"], 40.0)
        self.assertEqual(
            result.recovery_check["original_triggered_at"],
            self.t0.isoformat(),
        )

    def test_renewal_when_still_red_after_expiry(self) -> None:
        """A3: when window expires but user is still red, the window is renewed
        (not cleared) and a recovery check is performed."""
        router = SafetyRouter(store=self.store)
        router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        # Window expired, still red -> renew + recovery check
        result = router.evaluate(
            scope=_scope(), points=_sustained_red(40),
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 1),
        )
        # Recovery check should be present (not confirmed because still red)
        self.assertIsNotNone(result.recovery_check)
        self.assertFalse(result.recovery_check["recovery_confirmed"])
        # Original trigger time is preserved
        self.assertEqual(
            result.recovery_check["original_triggered_at"],
            self.t0.isoformat(),
        )

    def test_initialize_backfills_legacy_original_trigger_timestamp(self) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO safety_red_zone_events "
                "(user_id, triggered_at, original_triggered_at, safety_result_json, created_at) "
                "VALUES (?, ?, NULL, ?, ?)",
                ("u1", self.t0.isoformat(), '{"status":"red_zone"}', self.t0.isoformat()),
            )
        # Simulate application startup after the A3 migration.
        self.store.initialize()
        result = SafetyRouter(store=self.store).evaluate(
            scope=_scope(),
            points=_sustained_red(40),
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 1),
        )
        self.assertEqual(
            result.recovery_check["original_triggered_at"],
            self.t0.isoformat(),
        )


class RedZonePersistenceTests(unittest.TestCase):
    """TD2: red-zone recovery state must survive a process restart when a
    SQLiteStore is injected.  The in-memory path (no store) remains the
    backward-compatible default for unit tests."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "safety.db"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()
        self.t0 = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_red_zone_persists_across_router_instances(self) -> None:
        """A new SafetyRouter with the same store sees the prior red-zone event."""
        router_a = SafetyRouter(store=self.store)
        router_a.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)

        # Simulate process restart: create a brand-new router with the same DB.
        router_b = SafetyRouter(store=self.store)
        stored = router_b._get_stored_red_zone("u1")
        self.assertIsNotNone(stored, "red-zone state should survive router recreation")

    def test_recovery_check_works_after_restart(self) -> None:
        """After restart, a green evaluation within the window triggers recovery_confirmed."""
        router_a = SafetyRouter(store=self.store)
        router_a.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)

        router_b = SafetyRouter(store=self.store)
        result = router_b.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(minutes=30)
        )
        self.assertIsNotNone(result.recovery_check)
        self.assertTrue(result.recovery_check["recovery_confirmed"])

    def test_expired_red_zone_cleared_from_sqlite(self) -> None:
        """After the recovery window expires, the SQLite row is deleted."""
        router = SafetyRouter(store=self.store)
        router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)

        # Evaluate well past the recovery window with a normal value.
        router.evaluate(
            scope=_scope(), points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS + 60),
        )
        self.assertIsNone(router._get_stored_red_zone("u1"))

    def test_in_memory_mode_unchanged(self) -> None:
        """SafetyRouter() with no store still uses the dict -- backward compat."""
        router = SafetyRouter()
        router.evaluate(scope=_scope(), points=_sustained_red(40), now=self.t0)
        self.assertIn("u1", router._last_red_zone)
        self.assertIsNone(router._store)


if __name__ == "__main__":
    unittest.main()
