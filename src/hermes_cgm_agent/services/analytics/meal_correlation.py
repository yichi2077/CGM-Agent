"""Food-glucose correlation analysis (F2).

Given a meal event and the glucose points that follow it, this module computes
the postprandial glucose response (peak, time-to-peak, incremental AUC). It
also provides ``find_similar_meals`` to retrieve historical meal events by food
name and their associated glucose responses — the data behind the "先历史后知识"
narrative (e.g. "上次你吃类似的东西后，餐后大概两小时出现了一个小高峰").

Design notes:
- The analyzer is a pure-Python class with no side effects. It takes already-
  loaded ``UserEvent`` and ``list[GlucosePoint]`` objects, so it is trivially
  testable offline.
- ``find_similar_meals`` takes a ``SQLiteCGMRepository`` (duck-typed: anything
  with ``list_user_events`` and ``list_glucose_points`` works) and performs the
  historical lookup + response computation in one call.
- All glucose values are in mg/dL internally (the domain model's canonical
  unit). Display conversion happens at the report/narrative layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Protocol

from hermes_cgm_agent.domain import DataScope, GlucosePoint, UserEvent


# Default postprandial analysis window: 3 hours after meal start.
# The plan specifies 2-3 hours; 3h captures the full response tail.
DEFAULT_WINDOW_HOURS = 3

# Minimum points needed for a meaningful AUC computation.
_MIN_POINTS_FOR_AUC = 2
_MAX_PREMEAL_BASELINE_AGE = timedelta(minutes=30)


class _EventRepository(Protocol):
    """Duck-typed repository interface for ``find_similar_meals``."""

    def list_user_events(
        self,
        scope: DataScope,
        *,
        confirmed_only: bool = False,
        include_rejected: bool = False,
    ) -> list[UserEvent]: ...

    def list_glucose_points(self, scope: DataScope) -> list[GlucosePoint]: ...


@dataclass(frozen=True)
class MealGlucoseResponse:
    """Postprandial glucose response for a single meal.

    All glucose values are in mg/dL. Times are minutes relative to meal start.
    """

    peak_value_mg_dl: float | None
    """Highest glucose value observed in the postprandial window."""

    peak_time_minutes: float | None
    """Minutes from meal start to the peak value."""

    auc_mg_dl_min: float | None
    """Incremental AUC (area above baseline) in mg/dL * min."""

    baseline_value_mg_dl: float | None
    """Glucose value at or nearest before meal start."""

    delta_peak_mg_dl: float | None
    """Difference: peak_value - baseline_value."""

    point_count: int
    """Number of glucose points in the postprandial window."""

    window_minutes: float
    """Actual window covered (meal_start to last point), in minutes."""


@dataclass(frozen=True)
class SimilarMealResult:
    """A historical meal event and its glucose response."""

    event: UserEvent
    response: MealGlucoseResponse | None
    matched_food_name: str
    """The food name that matched the search query."""


class MealCorrelationAnalyzer:
    """Analyzes postprandial glucose response after meal events (F2).

    Usage::

        analyzer = MealCorrelationAnalyzer()
        response = analyzer.analyze_response(meal_event, glucose_points)
        similar = analyzer.find_similar_meals("面条", "user-1", repository)
    """

    def __init__(self, *, window_hours: int = DEFAULT_WINDOW_HOURS) -> None:
        self.window_hours = window_hours

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def analyze_response(
        self,
        meal_event: UserEvent,
        glucose_points: list[GlucosePoint],
        *,
        window_hours: int | None = None,
    ) -> MealGlucoseResponse:
        """Compute postprandial glucose response for a single meal.

        Args:
            meal_event: The meal ``UserEvent``. ``ts_start`` defines t=0.
            glucose_points: Glucose points to search (any time range; the
                method filters to the postprandial window internally).
            window_hours: Override the default analysis window.

        Returns:
            A :class:`MealGlucoseResponse` with peak, time-to-peak, iAUC, etc.
            If no points fall in the window, all numeric fields are ``None``
            and ``point_count`` is 0.
        """
        hours = window_hours if window_hours is not None else self.window_hours
        meal_start = meal_event.ts_start
        window_end = meal_start + timedelta(hours=hours)

        # Filter to postprandial window: [meal_start, meal_start + window_hours]
        postprandial = [
            p for p in glucose_points
            if p.user_id == meal_event.user_id
            and p.timestamp >= meal_start
            and p.timestamp < window_end
        ]
        postprandial.sort(key=lambda p: p.timestamp)

        if not postprandial:
            return MealGlucoseResponse(
                peak_value_mg_dl=None,
                peak_time_minutes=None,
                auc_mg_dl_min=None,
                baseline_value_mg_dl=None,
                delta_peak_mg_dl=None,
                point_count=0,
                window_minutes=0.0,
            )

        # Baseline: use a point at/just before the meal only when it is recent
        # enough to describe the meal context.  A reading from yesterday is not
        # a valid baseline for today's post-prandial response.
        all_pre_meal = [
            p for p in glucose_points
            if p.user_id == meal_event.user_id and p.timestamp <= meal_start
        ]
        pre_meal = [
            p for p in all_pre_meal
            if meal_start - p.timestamp <= _MAX_PREMEAL_BASELINE_AGE
        ]
        pre_meal.sort(key=lambda p: p.timestamp)
        if pre_meal:
            baseline: float | None = pre_meal[-1].value_mg_dl
        elif all_pre_meal:
            # There is historical data, but it is too stale to infer a
            # baseline; don't manufacture delta/iAUC values from it.
            baseline = None
        else:
            baseline = postprandial[0].value_mg_dl

        # Peak
        peak_point = max(postprandial, key=lambda p: p.value_mg_dl)
        peak_value = peak_point.value_mg_dl
        peak_time_min = (peak_point.timestamp - meal_start).total_seconds() / 60.0

        # Incremental AUC (trapezoidal, only area above baseline)
        auc = self._compute_iauc(postprandial, baseline) if baseline is not None else None

        # Actual window covered
        last_ts = postprandial[-1].timestamp
        window_min = (last_ts - meal_start).total_seconds() / 60.0

        return MealGlucoseResponse(
            peak_value_mg_dl=round(peak_value, 2),
            peak_time_minutes=round(peak_time_min, 1),
            auc_mg_dl_min=round(auc, 2) if auc is not None else None,
            baseline_value_mg_dl=round(baseline, 2) if baseline is not None else None,
            delta_peak_mg_dl=round(peak_value - baseline, 2) if baseline is not None else None,
            point_count=len(postprandial),
            window_minutes=round(window_min, 1),
        )

    # ------------------------------------------------------------------
    # Historical meal search
    # ------------------------------------------------------------------

    def find_similar_meals(
        self,
        food_name: str,
        user_id: str,
        repository: _EventRepository,
        *,
        limit: int = 10,
        window_hours: int | None = None,
    ) -> list[SimilarMealResult]:
        """Search historical meal events by food name and compute responses.

        Matches food names in:
        - ``payload["food_items"][i]["name"]`` (structured F1 fields)
        - ``payload["structured_summary"]`` (generated by F1 handler)
        - ``payload`` freeform text (tolerant fallback)

        Args:
            food_name: Food name to search for (e.g. "面条").
            user_id: User whose events to search.
            repository: A ``SQLiteCGMRepository`` (or compatible).
            limit: Maximum number of results.
            window_hours: Override the default analysis window for responses.

        Returns:
            List of :class:`SimilarMealResult`, most recent first.
        """
        # Guard against empty/whitespace food_name: an empty query would match
        # every meal event (substring "" is in every string), producing
        # meaningless results.
        if not food_name.strip():
            return []
        # Search a wide window (last 365 days) for historical meals.
        now = datetime.now(tz=timezone.utc)
        scope = DataScope(
            user_id=user_id,
            window_start=now - timedelta(days=365),
            window_end=now,
        )
        # Agent-created events are candidates until the user confirms them;
        # they must not be presented as the user's historical experience.
        all_events = repository.list_user_events(
            scope, confirmed_only=True, include_rejected=False
        )

        # Filter to meal events matching the food name
        results: list[SimilarMealResult] = []
        query_lower = food_name.lower().strip()

        for event in all_events:
            if event.event_type != "meal":
                continue
            matched = _match_food_name(event, query_lower)
            if matched is None:
                continue

            # Fetch glucose points for the postprandial window
            hours = window_hours if window_hours is not None else self.window_hours
            response = self._compute_response_for_event(
                event, repository, window_hours=hours
            )
            results.append(SimilarMealResult(
                event=event,
                response=response,
                matched_food_name=matched,
            ))

        # Sort by most recent first, then limit
        results.sort(key=lambda r: r.event.ts_start, reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_response_for_event(
        self,
        event: UserEvent,
        repository: _EventRepository,
        *,
        window_hours: int,
    ) -> MealGlucoseResponse | None:
        """Fetch glucose points around a meal event and compute its response."""
        meal_start = event.ts_start
        # Fetch a slightly wider window to capture pre-meal baseline points.
        scope = DataScope(
            user_id=event.user_id,
            window_start=meal_start - timedelta(minutes=30),
            window_end=meal_start + timedelta(hours=window_hours),
        )
        points = repository.list_glucose_points(scope)
        if not points:
            return None
        return self.analyze_response(event, points, window_hours=window_hours)

    @staticmethod
    def _compute_iauc(
        points: list[GlucosePoint],
        baseline: float,
    ) -> float | None:
        """Incremental AUC via trapezoidal rule (area above baseline only).

        Negative excursions (below baseline) are clamped to zero — this is the
        standard iAUC convention for postprandial glucose response.
        """
        if len(points) < _MIN_POINTS_FOR_AUC:
            return None
        total = 0.0
        for i in range(1, len(points)):
            dt_min = (
                points[i].timestamp - points[i - 1].timestamp
            ).total_seconds() / 60.0
            if dt_min <= 0:
                continue
            v_prev = max(0.0, points[i - 1].value_mg_dl - baseline)
            v_curr = max(0.0, points[i].value_mg_dl - baseline)
            total += (v_prev + v_curr) / 2.0 * dt_min
        return total


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _match_food_name(event: UserEvent, query_lower: str) -> str | None:
    """Check if a meal event's payload mentions the queried food name.

    Returns the matched food name (original case from the event), or ``None``
    if no match is found.
    """
    payload = event.payload or {}

    # 1. Structured food_items (F1)
    food_items = payload.get("food_items")
    if isinstance(food_items, list):
        for item in food_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if name and _matches_food_query(name, query_lower):
                return name

    # 2. Structured summary (F1 handler-generated)
    summary = payload.get("structured_summary")
    if isinstance(summary, str) and _matches_food_query(summary, query_lower):
        return query_lower

    # 3. Freeform payload fallback (e.g. {"description": "吃了面条"})
    for value in payload.values():
        if isinstance(value, str) and _matches_food_query(value, query_lower):
            return query_lower

    return None


def _matches_food_query(text: str, query: str) -> bool:
    """Use word boundaries for English, but preserve CJK substring matching."""
    normalized_query = query.casefold()
    if normalized_query.isascii() and any(char.isalnum() for char in normalized_query):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_query)}(?![a-z0-9])",
            text.casefold(),
        ) is not None
    return normalized_query in text.casefold()
