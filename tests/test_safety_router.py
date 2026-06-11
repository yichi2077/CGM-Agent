"""Tests for the three-zone safety router."""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
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

UTC = timezone.utc


def _point(value: float, unit: GlucoseUnit = GlucoseUnit.MG_DL) -> GlucosePoint:
    return GlucosePoint(
        user_id="u1",
        timestamp=datetime(2026, 6, 6, 12, 0, tzinfo=UTC),
        value=value,
        unit=unit,
        source="test",
        quality_flag=QualityFlag.VALID,
    )


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
        points = [_point(260)]  # above 250 but below 300
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertEqual(decision.safety_result["direction"], "偏高")

    def test_yellow_uses_mg_dl_not_raw_value(self) -> None:
        """BUG FIX: a mmol/L value of 3.5 (=63 mg/dL) must trigger yellow,
        not be compared raw against mg/dL thresholds."""
        points = [_point(3.5, unit=GlucoseUnit.MMOL_L)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        # 3.5 mmol/L = 63 mg/dL → below YELLOW_ZONE_LOW (70) → yellow
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
        points = [_point(50)]  # below 54
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertEqual(decision.route, "reports.generate.red_zone")

    def test_high_red_detected(self) -> None:
        points = [_point(350)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_red_uses_mg_dl_not_raw_value(self) -> None:
        """BUG FIX: a mmol/L value of 2.5 (=45 mg/dL) must trigger red."""
        points = [_point(2.5, unit=GlucoseUnit.MMOL_L)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_red_takes_precedence_over_yellow(self) -> None:
        """If both red and yellow points exist, red wins."""
        points = [_point(50), _point(65)]  # red + yellow
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(decision.safety_result["status"], "red_zone")

    def test_red_min_max_in_mg_dl(self) -> None:
        points = [_point(40), _point(350)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        result = decision.safety_result
        self.assertEqual(result["min_value_mgdl"], 40.0)
        self.assertEqual(result["max_value_mgdl"], 350.0)

    def test_red_min_max_with_mmol_input(self) -> None:
        """min/max values must be reported in mg/dL even when input is mmol/L."""
        points = [_point(2.5, unit=GlucoseUnit.MMOL_L), _point(20.0, unit=GlucoseUnit.MMOL_L)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        result = decision.safety_result
        # 2.5 mmol/L ≈ 45 mg/dL, 20.0 mmol/L ≈ 360.5 mg/dL
        self.assertLess(result["min_value_mgdl"], RED_ZONE_LOW_MGDL)
        self.assertGreater(result["max_value_mgdl"], RED_ZONE_HIGH_MGDL)

    def test_red_message_is_medical_deferral(self) -> None:
        points = [_point(50)]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertIn("医疗判断", decision.message)

    def test_red_evidence_refs_capped_at_5(self) -> None:
        points = [_point(v) for v in [40, 42, 44, 46, 48, 50, 52]]
        decision = SafetyRouter().evaluate(scope=_scope(), points=points)
        self.assertEqual(len(decision.evidence_refs), 5)


class RecoveryDoubleCheckTests(unittest.TestCase):
    """F3-B3 / US3 (analyze D1): a red-zone event arms a recovery double-check.

    A LATER evaluation within the 2-hour window compares the STORED original red
    result against the CURRENT result — it never re-evaluates the same data and
    never recurses into ``evaluate()``.
    """

    def setUp(self) -> None:
        self.router = SafetyRouter()
        self.t0 = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

    def test_recovery_confirmed_when_green_after_red_in_window(self) -> None:
        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(hours=1)
        )
        self.assertIsNotNone(later.recovery_check)
        self.assertEqual(later.recovery_check["original"]["status"], "red_zone")
        self.assertEqual(later.recovery_check["recovery"]["status"], "clear")
        self.assertTrue(later.recovery_check["recovery_confirmed"])
        self.assertTrue(later.recovery_check["active"])

    def test_no_recovery_after_window_expires_and_state_cleared(self) -> None:
        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
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
        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(40)], now=self.t0 + timedelta(hours=1)
        )
        self.assertIsNotNone(later.recovery_check)
        self.assertFalse(later.recovery_check["recovery_confirmed"])

    def test_original_equals_stored_t0_red_not_a_reeval(self) -> None:
        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
        later = self.router.evaluate(
            scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(hours=1)
        )
        self.assertEqual(later.recovery_check["original"]["min_value_mgdl"], 40.0)

    def test_window_boundary_exactly_at_limit_is_expired(self) -> None:
        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
        later = self.router.evaluate(
            scope=_scope(),
            points=[_point(100)],
            now=self.t0 + timedelta(seconds=RECOVERY_WINDOW_SECONDS),
        )
        self.assertIsNone(later.recovery_check)

    def test_env_override_changes_window(self) -> None:
        with mock.patch.dict(os.environ, {"CGM_AGENT_RECOVERY_WINDOW_SECONDS": "60"}):
            self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
            expired = self.router.evaluate(
                scope=_scope(), points=[_point(100)], now=self.t0 + timedelta(seconds=61)
            )
            self.assertIsNone(expired.recovery_check)
            self.router.evaluate(
                scope=_scope(), points=[_point(40)], now=self.t0 + timedelta(minutes=10)
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
        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
        self.assertEqual(len(self.router._last_red_zone), 1)

    def test_internal_red_zone_state_is_not_serialized(self) -> None:
        # SEC-004 / T021: the router's private last-red-zone timestamp state must
        # never leak into a serialized SafetyDecision (no raw datetimes, no
        # internal dict name).
        from dataclasses import asdict
        from datetime import datetime as _dt

        self.router.evaluate(scope=_scope(), points=[_point(40)], now=self.t0)
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


if __name__ == "__main__":
    unittest.main()
