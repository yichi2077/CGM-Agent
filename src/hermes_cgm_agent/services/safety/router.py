from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime

from hermes_cgm_agent.domain import DataScope, EvidenceKind, EvidenceRef, GlucosePoint
from hermes_cgm_agent.domain.cgm import utc_now


# ── recovery double-check (F3-B3, analyze D1) ─────────────────────
# After a red-zone event, a later evaluation within this window performs a
# recovery double-check comparing the stored original red result against the
# current result. In-memory, per-process, per-user state (acceptable for a
# single-user personal deployment); not persisted.
RECOVERY_WINDOW_SECONDS = 7200  # 2 hours
RECOVERY_WINDOW_ENV = "CGM_AGENT_RECOVERY_WINDOW_SECONDS"


def _recovery_window_seconds() -> int:
    raw = os.environ.get(RECOVERY_WINDOW_ENV, "").strip()
    if not raw:
        return RECOVERY_WINDOW_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return RECOVERY_WINDOW_SECONDS


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
    # F3-B3 (US3): set when a red-zone recovery window is active. None otherwise.
    # Backward-compatible — callers that ignore it are unaffected.
    recovery_check: dict[str, object] | None = None


class SafetyRouter:
    """Three-zone safety router: green → yellow → red.

    All comparisons use ``point.value_mg_dl`` (always mg/dL) to avoid
    unit-mismatch bugs when the source data arrives in mmol/L.

    Stateful (F3-B3): the router remembers the last red-zone event per user to
    drive the recovery double-check. Instantiate it ONCE per process (or inject
    it) so the 2-hour window state survives across ``evaluate`` calls (analyze
    U2) — ``ReportService`` already holds a single instance for its lifetime.
    """

    def __init__(self) -> None:
        # user_id -> (timestamp of the red-zone event, its safety_result dict)
        self._last_red_zone: dict[str, tuple[datetime, dict[str, object]]] = {}

    def evaluate(
        self,
        *,
        scope: DataScope,
        points: list[GlucosePoint],
        now: datetime | None = None,
    ) -> SafetyDecision:
        # Single, NON-recursive zone decision (analyze D1 — never call evaluate()
        # from within evaluate()).
        decision = self._evaluate_zone(scope=scope, points=points)
        now = now or utc_now()
        user_id = scope.user_id
        status = decision.safety_result.get("status")
        window = _recovery_window_seconds()

        stored = self._last_red_zone.get(user_id)
        active_stored: tuple[datetime, dict[str, object]] | None = None
        if stored is not None:
            if (now - stored[0]).total_seconds() < window:
                active_stored = stored
            else:
                # window expired → forget the red-zone event entirely
                del self._last_red_zone[user_id]

        if active_stored is not None:
            # Compare the STORED original red result against the CURRENT result.
            # recovery_confirmed iff the user is no longer in the red zone.
            recovery_check = self._build_recovery_check(
                now=now,
                stored=active_stored,
                current=decision.safety_result,
                confirmed=status != "red_zone",
                window=window,
            )
            # Keep the original red baseline; do not overwrite while active.
            return replace(decision, recovery_check=recovery_check)

        # No active prior red-zone window. Record a fresh red event so a later
        # evaluation can run the recovery double-check.
        if status == "red_zone":
            self._last_red_zone[user_id] = (now, decision.safety_result)
        return decision

    @staticmethod
    def _build_recovery_check(
        *,
        now: datetime,
        stored: tuple[datetime, dict[str, object]],
        current: dict[str, object],
        confirmed: bool,
        window: int,
    ) -> dict[str, object]:
        stored_ts, stored_result = stored
        remaining = window - (now - stored_ts).total_seconds()
        return {
            "active": True,
            "window_remaining_seconds": max(0, int(remaining)),
            "original": stored_result,
            "recovery": current,
            "recovery_confirmed": confirmed,
        }

    def _evaluate_zone(
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
