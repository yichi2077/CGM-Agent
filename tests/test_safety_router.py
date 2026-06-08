"""Tests for the three-zone safety router."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from hermes_cgm_agent.domain import DataScope, EvidenceKind, GlucosePoint, GlucoseUnit, QualityFlag
from hermes_cgm_agent.services.safety.router import (
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


if __name__ == "__main__":
    unittest.main()
