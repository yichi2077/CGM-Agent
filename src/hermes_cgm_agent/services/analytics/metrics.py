from __future__ import annotations

import math
from dataclasses import dataclass

from hermes_cgm_agent.domain import (
    DataScope,
    GlucoseAggregate,
    GlucosePoint,
    WindowLabel,
)


@dataclass(frozen=True)
class AnalyticsConfig:
    low_threshold_mg_dl: float = 70
    high_threshold_mg_dl: float = 180
    expected_interval_minutes: int = 5
    conga_interval_hours: int = 1
    included_quality_flags: tuple[str, ...] = ("valid",)


class CGMAnalyticsService:
    def __init__(self, config: AnalyticsConfig | None = None) -> None:
        self.config = config or AnalyticsConfig()

    def compute_aggregate(
        self,
        *,
        points: list[GlucosePoint],
        scope: DataScope,
        window_label: WindowLabel | str | None = None,
    ) -> GlucoseAggregate:
        eligible_points = self._eligible_points(points, scope)
        values = [point.value_mg_dl for point in eligible_points]
        point_count = len(values)
        expected_count = self._expected_point_count(scope)
        # Denser-than-expected sampling (e.g. a 1-minute-cadence device measured
        # against the 5-minute default) means FULL coverage, not >100%. Left
        # unclamped this overflowed GlucoseAggregate's le=100 constraint and
        # crashed every aggregate over dense data with a ValidationError.
        data_coverage = min(100.0, _percentage(point_count, expected_count))

        if point_count == 0:
            return GlucoseAggregate(
                user_id=scope.user_id,
                window_start=scope.window_start,
                window_end=scope.window_end,
                window_label=window_label,
                TIR=0,
                TAR=0,
                TBR=0,
                GMI=None,
                CV=None,
                MBG=None,
                data_coverage=data_coverage,
                point_count=0,
            )

        tbr_count = sum(1 for value in values if value < self.config.low_threshold_mg_dl)
        tar_count = sum(1 for value in values if value > self.config.high_threshold_mg_dl)
        tir_count = point_count - tbr_count - tar_count
        mean_glucose = sum(values) / point_count
        standard_deviation = _population_std(values, mean_glucose)
        cv = (standard_deviation / mean_glucose * 100) if mean_glucose > 0 else None
        gmi = 3.31 + (0.02392 * mean_glucose)
        lbgi, hbgi = _blood_glucose_risk_index(values)
        mage = _mean_amplitude_of_glycemic_excursions(eligible_points)
        modd = _mean_of_daily_differences(eligible_points)
        conga = _continuous_overlapping_net_glycemic_action(
            eligible_points,
            interval_hours=self.config.conga_interval_hours,
        )

        return GlucoseAggregate(
            user_id=scope.user_id,
            window_start=scope.window_start,
            window_end=scope.window_end,
            window_label=window_label,
            TIR=_percentage(tir_count, point_count),
            TAR=_percentage(tar_count, point_count),
            TBR=_percentage(tbr_count, point_count),
            GMI=_round(gmi),
            CV=_round(cv),
            MBG=_round(mean_glucose),
            LBGI=_round(lbgi),
            HBGI=_round(hbgi),
            MAGE=_round(mage),
            MODD=_round(modd),
            CONGA=_round(conga),
            data_coverage=data_coverage,
            point_count=point_count,
        )

    def _eligible_points(
        self,
        points: list[GlucosePoint],
        scope: DataScope,
    ) -> list[GlucosePoint]:
        return [
            point
            for point in points
            if point.user_id == scope.user_id
            and scope.window_start <= point.timestamp < scope.window_end
            and (scope.source is None or point.source == scope.source)
            and str(point.quality_flag) in self.config.included_quality_flags
        ]

    def _expected_point_count(self, scope: DataScope) -> int:
        duration_seconds = (scope.window_end - scope.window_start).total_seconds()
        interval_seconds = self.config.expected_interval_minutes * 60
        if duration_seconds <= 0 or interval_seconds <= 0:
            return 0
        return max(1, math.ceil(duration_seconds / interval_seconds))


def _percentage(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return _round((numerator / denominator) * 100)


def _population_std(values: list[float], mean: float) -> float:
    if not values:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _blood_glucose_risk_index(values_mg_dl: list[float]) -> tuple[float | None, float | None]:
    """Kovatchev Low/High Blood Glucose Risk Index (LBGI/HBGI).

    Reference: Kovatchev BP et al., symmetrization of the BG scale. The risk
    function is computed on mg/dL and clamped to the validated [20, 600] range
    so a single extreme reading cannot dominate the index.

    f(BG)  = 1.509 * (ln(BG)^1.084 - 5.381)
    rl(BG) = 10 * f^2  when f < 0 else 0
    rh(BG) = 10 * f^2  when f > 0 else 0
    LBGI   = mean(rl), HBGI = mean(rh)
    """
    if not values_mg_dl:
        return None, None
    low_risks: list[float] = []
    high_risks: list[float] = []
    for value in values_mg_dl:
        clamped = min(600.0, max(20.0, value))
        f = 1.509 * (math.log(clamped) ** 1.084 - 5.381)
        risk = 10 * f * f
        if f < 0:
            low_risks.append(risk)
            high_risks.append(0.0)
        elif f > 0:
            low_risks.append(0.0)
            high_risks.append(risk)
        else:
            low_risks.append(0.0)
            high_risks.append(0.0)
    lbgi = sum(low_risks) / len(low_risks)
    hbgi = sum(high_risks) / len(high_risks)
    return lbgi, hbgi


def _mean_amplitude_of_glycemic_excursions(points: list[GlucosePoint]) -> float | None:
    """Mean amplitude of excursions between alternating local extrema.

    This implementation uses the common operational rule: identify direction
    changes in the glucose series, then average peak-to-nadir amplitudes that
    exceed one population standard deviation for the same window.
    """
    ordered = sorted(points, key=lambda point: point.timestamp)
    values = [point.value_mg_dl for point in ordered]
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    threshold = _population_std(values, mean)
    if threshold <= 0:
        return 0.0

    extrema = [values[0]]
    last_direction = 0
    for previous, current in zip(values, values[1:]):
        direction = 1 if current > previous else -1 if current < previous else 0
        if direction == 0:
            continue
        if last_direction and direction != last_direction:
            extrema.append(previous)
        last_direction = direction
    extrema.append(values[-1])

    excursions = [
        abs(current - previous)
        for previous, current in zip(extrema, extrema[1:])
        if abs(current - previous) >= threshold
    ]
    if not excursions:
        return 0.0
    return sum(excursions) / len(excursions)


def _mean_of_daily_differences(points: list[GlucosePoint]) -> float | None:
    """MODD: mean absolute difference between adjacent days at the same clock time."""
    ordered = sorted(points, key=lambda point: point.timestamp)
    if len(ordered) < 2:
        return None
    by_day_time: dict[tuple[object, object], float] = {
        (point.timestamp.date(), point.timestamp.timetz().replace(tzinfo=None)): point.value_mg_dl
        for point in ordered
    }
    days = sorted({point.timestamp.date() for point in ordered})
    if len(days) < 2:
        return None

    differences: list[float] = []
    for previous_day, current_day in zip(days, days[1:]):
        times = {
            time_key
            for day_key, time_key in by_day_time
            if day_key in {previous_day, current_day}
        }
        for time_key in times:
            previous = by_day_time.get((previous_day, time_key))
            current = by_day_time.get((current_day, time_key))
            if previous is not None and current is not None:
                differences.append(abs(current - previous))
    if not differences:
        return None
    return sum(differences) / len(differences)


def _continuous_overlapping_net_glycemic_action(
    points: list[GlucosePoint],
    *,
    interval_hours: int,
) -> float | None:
    """CONGA-n: population SD of glucose differences n hours apart."""
    if interval_hours <= 0:
        return None
    ordered = sorted(points, key=lambda point: point.timestamp)
    if len(ordered) < 2:
        return None
    by_timestamp = {point.timestamp: point.value_mg_dl for point in ordered}
    from datetime import timedelta

    interval = timedelta(hours=interval_hours)
    differences: list[float] = []
    for point in ordered:
        previous = by_timestamp.get(point.timestamp - interval)
        if previous is not None:
            differences.append(point.value_mg_dl - previous)
    if not differences:
        return None
    mean_difference = sum(differences) / len(differences)
    return _population_std(differences, mean_difference)


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)
