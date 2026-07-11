from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from hermes_cgm_agent.domain import DataScope, EvidenceKind, EvidenceRef, GlucosePoint, ensure_utc
from hermes_cgm_agent.domain.cgm import utc_now

if TYPE_CHECKING:
    from hermes_cgm_agent.storage.sqlite import SQLiteStore


# -- recovery double-check (F3-B3, analyze D1) -------------------------------
# After a red-zone event, a later evaluation within this window performs a
# recovery double-check comparing the stored original red result against the
# current result. When a SQLiteStore is injected (TD2), state is persisted to
# survive process restarts; otherwise an in-memory dict is used.
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


# -- thresholds (all mg/dL) ---------------------------------------------------
# A1: yellow-zone upper bound aligned with AGP 2019 international consensus
# and metrics.py (high_threshold_mg_dl = 180).  New scheme:
#   green   70 - 180
#   yellow  low side 54 - 70,  high side 180 - 250
#   red     low side < 54,     high side > 250
RED_ZONE_LOW_MGDL = 54.0
RED_ZONE_HIGH_MGDL = 250.0

YELLOW_ZONE_LOW_MGDL = 70.0
YELLOW_ZONE_HIGH_MGDL = 180.0

# -- templates ----------------------------------------------------------------
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
    # Backward-compatible -- callers that ignore it are unaffected.
    recovery_check: dict[str, object] | None = None
    # A2: True when a transient red-zone anomaly (< 10 min sustained) was
    # downgraded to yellow.  Backward-compatible default.
    transient_suppressed: bool = False


class SafetyRouter:
    """Three-zone safety router: green -> yellow -> red.

    All comparisons use ``point.value_mg_dl`` (always mg/dL) to avoid
    unit-mismatch bugs when the source data arrives in mmol/L.

    Stateful (F3-B3): the router remembers the last red-zone event per user to
    drive the recovery double-check. Instantiate it ONCE per process (or inject
    it) so the 2-hour window state survives across ``evaluate`` calls (analyze
    U2) -- ``ReportService`` already holds a single instance for its lifetime.
    """

    def __init__(self, *, store: "SQLiteStore | None" = None) -> None:
        # TD2: when a SQLiteStore is injected, red-zone state is persisted so
        # the recovery window survives process restart.  Without a store the
        # in-memory dict is used (backward-compatible default for unit tests).
        self._store = store
        # user_id -> (original_triggered_at, triggered_at, safety_result dict)
        self._last_red_zone: dict[str, tuple[datetime, datetime, dict[str, object]]] = {}
        # M-01: guard in-memory red-zone state against concurrent access
        # when the memory path (no store) is used.
        self._red_zone_lock = threading.Lock()

    # -- red-zone state accessors (TD2, A3) ----------------------------------

    def _get_stored_red_zone(
        self, user_id: str
    ) -> tuple[datetime, datetime, dict[str, object]] | None:
        """Read the red-zone state from SQLite or the in-memory dict.

        Returns ``(original_triggered_at, triggered_at, safety_result)`` or
        ``None``.  ``original_triggered_at`` is the first trigger time and is
        preserved across window renewals; ``triggered_at`` is the most recent
        renewal time used for window-expiry calculations.
        """
        if self._store is not None:
            with self._store.connect() as conn:
                row = conn.execute(
                    "SELECT triggered_at, original_triggered_at, safety_result_json "
                    "FROM safety_red_zone_events WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            if row is None:
                return None
            triggered_ts = datetime.fromisoformat(row["triggered_at"])
            col_keys = row.keys()
            original_str = (
                row["original_triggered_at"]
                if "original_triggered_at" in col_keys
                else None
            )
            if original_str:
                original_ts = datetime.fromisoformat(original_str)
            else:
                original_ts = triggered_ts
            return (original_ts, triggered_ts, json.loads(row["safety_result_json"]))
        return self._last_red_zone.get(user_id)

    def _store_red_zone(
        self, user_id: str, ts: datetime, result: dict[str, object]
    ) -> None:
        """Write a fresh red-zone state (original = triggered = ts)."""
        if self._store is not None:
            with self._store.connect() as conn:
                conn.execute(
                    """INSERT INTO safety_red_zone_events
                           (user_id, triggered_at, original_triggered_at,
                            safety_result_json, created_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET
                         triggered_at = excluded.triggered_at,
                         original_triggered_at = excluded.original_triggered_at,
                         safety_result_json = excluded.safety_result_json,
                         created_at = excluded.created_at""",
                    (user_id, ts.isoformat(), ts.isoformat(),
                     json.dumps(result, ensure_ascii=False),
                     utc_now().isoformat()),
                )
        else:
            self._last_red_zone[user_id] = (ts, ts, result)

    def _clear_stored_red_zone(self, user_id: str) -> None:
        """Clear an expired red-zone state from SQLite or the in-memory dict."""
        if self._store is not None:
            with self._store.connect() as conn:
                conn.execute(
                    "DELETE FROM safety_red_zone_events WHERE user_id = ?",
                    (user_id,),
                )
        else:
            self._last_red_zone.pop(user_id, None)

    # -- evaluation -----------------------------------------------------------

    def evaluate(
        self,
        *,
        scope: DataScope,
        points: list[GlucosePoint],
        now: datetime | None = None,
    ) -> SafetyDecision:
        # Single, NON-recursive zone decision (analyze D1 -- never call evaluate()
        # from within evaluate()).
        decision = self._evaluate_zone(scope=scope, points=points)
        # naive == UTC (D051): a naive `now` subtracted from the aware stored
        # red-zone timestamp would raise TypeError mid-report.
        now = ensure_utc(now or utc_now())
        user_id = scope.user_id
        status = decision.safety_result.get("status")
        window = _recovery_window_seconds()

        if self._store is not None:
            # M-23: use transaction() instead of connect() so the
            # read-check-write sequence is fully atomic (all writes commit
            # or all roll back together). Nested connect() calls in
            # _get_stored_red_zone etc. reuse this transaction's connection.
            with self._store.transaction() as conn:
                row = conn.execute(
                    "SELECT triggered_at, original_triggered_at, safety_result_json "
                    "FROM safety_red_zone_events WHERE user_id = ?",
                    (user_id,),
                ).fetchone()

                active_stored: (
                    tuple[datetime, datetime, dict[str, object]] | None
                ) = None
                if row is not None:
                    triggered_ts = datetime.fromisoformat(row["triggered_at"])
                    col_keys = row.keys()
                    original_str = (
                        row["original_triggered_at"]
                        if "original_triggered_at" in col_keys
                        else None
                    )
                    if original_str:
                        original_ts = datetime.fromisoformat(original_str)
                    else:
                        original_ts = triggered_ts
                    stored_result = json.loads(row["safety_result_json"])

                    if (now - triggered_ts).total_seconds() < window:
                        # Window still active -- no write needed.
                        active_stored = (original_ts, triggered_ts, stored_result)
                    elif status == "red_zone":
                        # A3: window expired but user is still red -> renew the
                        # window by updating triggered_at only.  Keep
                        # original_triggered_at and safety_result_json so the
                        # recovery double-check baseline is the ORIGINAL event.
                        conn.execute(
                            "UPDATE safety_red_zone_events "
                            "SET triggered_at = ? WHERE user_id = ?",
                            (now.isoformat(), user_id),
                        )
                        active_stored = (original_ts, now, stored_result)
                    else:
                        # Window expired and user is no longer red -> clear.
                        conn.execute(
                            "DELETE FROM safety_red_zone_events "
                            "WHERE user_id = ?",
                            (user_id,),
                        )

                if active_stored is not None:
                    recovery_check = self._build_recovery_check(
                        now=now,
                        stored=active_stored,
                        current=decision.safety_result,
                        confirmed=status != "red_zone",
                        window=window,
                    )
                    return replace(decision, recovery_check=recovery_check)

                # No active prior window -- record a fresh red event.
                if status == "red_zone":
                    conn.execute(
                        """INSERT INTO safety_red_zone_events
                               (user_id, triggered_at, original_triggered_at,
                                safety_result_json, created_at)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                             triggered_at = excluded.triggered_at,
                             original_triggered_at = excluded.original_triggered_at,
                             safety_result_json = excluded.safety_result_json,
                             created_at = excluded.created_at""",
                        (user_id, now.isoformat(), now.isoformat(),
                         json.dumps(decision.safety_result, ensure_ascii=False),
                         utc_now().isoformat()),
                    )
        else:
            # In-memory path (backward-compatible default for unit tests).
            # M-01 fix: use _red_zone_lock to guard the read-check-write
            # sequence against concurrent evaluate() calls on the same user.
            with self._red_zone_lock:
                stored = self._last_red_zone.get(user_id)
                active_stored: (
                    tuple[datetime, datetime, dict[str, object]] | None
                ) = None
                if stored is not None:
                    original_ts, triggered_ts, stored_result = stored
                    if (now - triggered_ts).total_seconds() < window:
                        active_stored = (original_ts, triggered_ts, stored_result)
                    elif status == "red_zone":
                        # A3: renew -- update triggered_at, keep original.
                        self._last_red_zone[user_id] = (original_ts, now, stored_result)
                        active_stored = (original_ts, now, stored_result)
                    else:
                        self._last_red_zone.pop(user_id, None)

                if active_stored is not None:
                    recovery_check = self._build_recovery_check(
                        now=now,
                        stored=active_stored,
                        current=decision.safety_result,
                        confirmed=status != "red_zone",
                        window=window,
                    )
                    return replace(decision, recovery_check=recovery_check)

                if status == "red_zone":
                    self._last_red_zone[user_id] = (now, now, decision.safety_result)

        return decision

    @staticmethod
    def _build_recovery_check(
        *,
        now: datetime,
        stored: tuple[datetime, datetime, dict[str, object]],
        current: dict[str, object],
        confirmed: bool,
        window: int,
    ) -> dict[str, object]:
        original_ts, triggered_ts, stored_result = stored
        remaining = window - (now - triggered_ts).total_seconds()
        return {
            "active": True,
            "window_remaining_seconds": max(0, int(remaining)),
            "original": stored_result,
            "recovery": current,
            "recovery_confirmed": confirmed,
            # A3: expose the original trigger time so callers can see how long
            # ago the red-zone episode actually started (survives renewals).
            "original_triggered_at": original_ts.isoformat(),
        }

    def _evaluate_zone(
        self,
        *,
        scope: DataScope,
        points: list[GlucosePoint],
    ) -> SafetyDecision:
        if not points:
            return self._green()

        def is_red(p: GlucosePoint) -> bool:
            return (
                p.value_mg_dl < RED_ZONE_LOW_MGDL
                or p.value_mg_dl > RED_ZONE_HIGH_MGDL
            )

        def is_yellow(p: GlucosePoint) -> bool:
            return (
                p.value_mg_dl < YELLOW_ZONE_LOW_MGDL
                or p.value_mg_dl > YELLOW_ZONE_HIGH_MGDL
            )

        # -- red zone scan (A2: time-gated) --------------------------------
        red_points = [p for p in points if is_red(p)]
        if red_points:
            if self._has_sustained_zone(points, is_red, min_minutes=10):
                return self._red(red_points, scope)
            # A2: transient red (< 10 min sustained) -> downgrade to yellow.
            # Include the red points in the yellow set so the alert still
            # surfaces the actual out-of-range values.
            yellow_points = [p for p in points if is_yellow(p) or is_red(p)]
            if yellow_points:
                decision = self._yellow(yellow_points)
                return replace(decision, transient_suppressed=True)
            # Fallback: should not happen (red points exist), but be safe.
            return replace(self._green(), transient_suppressed=True)

        # -- yellow zone scan ----------------------------------------------
        yellow_points = [p for p in points if is_yellow(p)]
        if yellow_points:
            return self._yellow(yellow_points)

        # -- green (clear) -------------------------------------------------
        return self._green()

    @staticmethod
    def _has_sustained_zone(
        points: list[GlucosePoint],
        predicate: Callable[[GlucosePoint], bool],
        min_minutes: float = 10.0,
        max_gap_minutes: float | None = None,
    ) -> bool:
        """Check whether any consecutive run of predicate-satisfying points
        spans at least *min_minutes* (first-to-last timestamp).

        Points are sorted by timestamp before scanning.  A run is a maximal
        sequence of consecutive points (in time order) where every point
        satisfies the predicate and adjacent qualifying samples are close
        enough to establish continuity.  A single point has duration 0 and is
        always transient.  By default, a data gap longer than the required
        duration breaks a run: two isolated readings an hour apart are not
        proof that the abnormal state persisted for that hour.
        """
        sorted_pts = sorted(points, key=lambda p: p.timestamp)
        run_start: datetime | None = None
        run_end: datetime | None = None
        threshold_seconds = min_minutes * 60
        # Cycle2: the safety router uses a 10-minute gap threshold (min_minutes),
        # which is intentionally stricter than the event detector's ~20-minute
        # gap threshold. The safety router is conservative — a shorter gap
        # breaks the sustained-zone run, downgrading a potentially stale red
        # zone to yellow. The event detector uses a wider gap to avoid
        # fragmenting behavioral patterns. This asymmetry is by design.
        max_gap_seconds = (max_gap_minutes if max_gap_minutes is not None else min_minutes) * 60
        for p in sorted_pts:
            if predicate(p):
                if (
                    run_start is None
                    or run_end is None
                    or (p.timestamp - run_end).total_seconds() > max_gap_seconds
                ):
                    # C-01: a data gap breaks the current run.  Before
                    # resetting, check whether the previous run already
                    # reached the sustained threshold — otherwise a real
                    # ≥10-minute red-zone episode followed by a sensor gap
                    # and more red points is silently downgraded to yellow.
                    if (
                        run_start is not None
                        and run_end is not None
                        and (run_end - run_start).total_seconds() >= threshold_seconds
                    ):
                        return True
                    run_start = p.timestamp
                run_end = p.timestamp
            else:
                if (
                    run_start is not None
                    and run_end is not None
                    and (run_end - run_start).total_seconds() >= threshold_seconds
                ):
                    return True
                run_start = None
                run_end = None
        # Check the final run.
        if (
            run_start is not None
            and run_end is not None
            and (run_end - run_start).total_seconds() >= threshold_seconds
        ):
            return True
        return False

    # -- private builders -----------------------------------------------------

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
