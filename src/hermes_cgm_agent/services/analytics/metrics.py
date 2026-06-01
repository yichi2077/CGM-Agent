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
        data_coverage = _percentage(point_count, expected_count)

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


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)
