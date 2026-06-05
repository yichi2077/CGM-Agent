from __future__ import annotations

from dataclasses import dataclass

from hermes_cgm_agent.domain import DataScope, EvidenceRef, GlucosePoint


RED_ZONE_LOW_MGDL = 54.0
RED_ZONE_HIGH_MGDL = 300.0
RED_ZONE_TEMPLATE = (
    "这个问题涉及医疗判断，我无法代替医生给出建议。"
    "我可以帮你整理相关数据，你可以在复诊时带给医生。需要我生成报告吗？"
)


@dataclass(frozen=True)
class SafetyDecision:
    route: str
    safety_result: dict[str, object]
    message: str | None = None
    evidence_refs: list[EvidenceRef] | None = None


class SafetyRouter:
    def evaluate(
        self,
        *,
        scope: DataScope,
        points: list[GlucosePoint],
    ) -> SafetyDecision:
        red_points = [
            point
            for point in points
            if point.value < RED_ZONE_LOW_MGDL or point.value > RED_ZONE_HIGH_MGDL
        ]
        if not red_points:
            return SafetyDecision(
                route="reports.generate",
                safety_result={
                    "status": "clear",
                    "reason": "no_red_zone_points",
                },
            )

        min_value = min(point.value for point in red_points)
        max_value = max(point.value for point in red_points)
        evidence_refs = [
            EvidenceRef(
                kind="glucose_point",
                ref_id=f"glucose:{point.user_id}:{point.timestamp.isoformat()}",
                summary=f"{point.timestamp.isoformat()} {point.value} {point.unit}",
            )
            for point in red_points[:5]
        ]
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
                "min_value_mgdl": min_value,
                "max_value_mgdl": max_value,
                "window_start": scope.window_start.isoformat(),
                "window_end": scope.window_end.isoformat(),
            },
        )
