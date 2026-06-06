from __future__ import annotations

from dataclasses import dataclass, field

from hermes_cgm_agent.domain import DataScope, EvidenceKind, EvidenceRef, GlucosePoint


# ── thresholds (all mg/dL) ────────────────────────────────────────
# 🔴 Red zone: immediate medical-deferral
RED_ZONE_LOW_MGDL = 54.0
RED_ZONE_HIGH_MGDL = 300.0

# 🟡 Yellow zone: alert prefix, normal narrative continues
YELLOW_ZONE_LOW_MGDL = 70.0
YELLOW_ZONE_HIGH_MGDL = 250.0

# ── templates ─────────────────────────────────────────────────────
RED_ZONE_TEMPLATE = (
    "这个问题涉及医疗判断，我无法代替医生给出建议。"
    "我可以帮你整理相关数据，你可以在复诊时带给医生。需要我生成报告吗？"
)

YELLOW_ZONE_TEMPLATE = (
    "⚠️ 当前血糖处于{direction}范围（{value:.0f} mg/dL），"
    "建议关注。以下是整体情况——"
)


@dataclass(frozen=True)
class SafetyDecision:
    route: str
    safety_result: dict[str, object]
    message: str | None = None
    evidence_refs: list[EvidenceRef] | None = None


class SafetyRouter:
    """Three-zone safety router: green → yellow → red.

    All comparisons use ``point.value_mg_dl`` (always mg/dL) to avoid
    unit-mismatch bugs when the source data arrives in mmol/L.
    """

    def evaluate(
        self,
        *,
        scope: DataScope,
        points: list[GlucosePoint],
    ) -> SafetyDecision:
        if not points:
            return self._green()

        # ── red zone scan ──────────────────────────────────────────
        red_points = [
            p for p in points
            if p.value_mg_dl < RED_ZONE_LOW_MGDL or p.value_mg_dl > RED_ZONE_HIGH_MGDL
        ]
        if red_points:
            return self._red(red_points, scope)

        # ── yellow zone scan ───────────────────────────────────────
        yellow_points = [
            p for p in points
            if p.value_mg_dl < YELLOW_ZONE_LOW_MGDL or p.value_mg_dl > YELLOW_ZONE_HIGH_MGDL
        ]
        if yellow_points:
            return self._yellow(yellow_points)

        # ── green (clear) ──────────────────────────────────────────
        return self._green()

    # ── private builders ───────────────────────────────────────────

    @staticmethod
    def _green() -> SafetyDecision:
        return SafetyDecision(
            route="reports.generate",
            safety_result={
                "status": "clear",
                "reason": "no_red_or_yellow_zone_points",
            },
        )

    @staticmethod
    def _red(
        red_points: list[GlucosePoint],
        scope: DataScope,
    ) -> SafetyDecision:
        values_mgdl = [p.value_mg_dl for p in red_points]
        min_val = min(values_mgdl)
        max_val = max(values_mgdl)
        evidence_refs = [
            EvidenceRef(
                kind=EvidenceKind.GLUCOSE_POINT,
                ref_id=f"glucose:{p.user_id}:{p.timestamp.isoformat()}",
                summary=f"{p.timestamp.isoformat()} {p.value_mg_dl} mg/dL",
            )
            for p in red_points[:5]
        ]
        # pick a representative value for the template
        rep_value = min_val if min_val < RED_ZONE_LOW_MGDL else max_val
        direction = "极低" if min_val < RED_ZONE_LOW_MGDL else "极高"
        return SafetyDecision(
            route="reports.generate.red_zone",
            message=RED_ZONE_TEMPLATE,
            evidence_refs=evidence_refs,
            safety_result={
                "status": "red_zone",
                "reason": "glucose_red_zone_detected",
                "template": RED_ZONE_TEMPLATE,
                "thresholds": {
                    "low_mgdl": RED_ZONE_LOW_MGDL,
                    "high_mgdl": RED_ZONE_HIGH_MGDL,
                },
                "trigger_count": len(red_points),
                "min_value_mgdl": min_val,
                "max_value_mgdl": max_val,
                "rep_direction": direction,
                "window_start": scope.window_start.isoformat(),
                "window_end": scope.window_end.isoformat(),
            },
        )

    @staticmethod
    def _yellow(yellow_points: list[GlucosePoint]) -> SafetyDecision:
        values_mgdl = [p.value_mg_dl for p in yellow_points]
        min_val = min(values_mgdl)
        max_val = max(values_mgdl)
        # determine direction for the template
        if min_val < YELLOW_ZONE_LOW_MGDL:
            direction = "偏低"
            rep_value = min_val
        else:
            direction = "偏高"
            rep_value = max_val
        message = YELLOW_ZONE_TEMPLATE.format(direction=direction, value=rep_value)
        evidence_refs = [
            EvidenceRef(
                kind=EvidenceKind.GLUCOSE_POINT,
                ref_id=f"glucose:{p.user_id}:{p.timestamp.isoformat()}",
                summary=f"{p.timestamp.isoformat()} {p.value_mg_dl} mg/dL",
            )
            for p in yellow_points[:3]
        ]
        return SafetyDecision(
            route="reports.generate",
            message=message,
            evidence_refs=evidence_refs,
            safety_result={
                "status": "yellow_zone",
                "reason": "glucose_yellow_zone_detected",
                "template": YELLOW_ZONE_TEMPLATE,
                "thresholds": {
                    "low_mgdl": YELLOW_ZONE_LOW_MGDL,
                    "high_mgdl": YELLOW_ZONE_HIGH_MGDL,
                },
                "trigger_count": len(yellow_points),
                "min_value_mgdl": min_val,
                "max_value_mgdl": max_val,
                "direction": direction,
                "rep_value_mgdl": rep_value,
            },
        )
