from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes_cgm_agent.domain import DataScope, GlucosePoint
from hermes_cgm_agent.domain.cgm import utc_now


@dataclass(frozen=True)
class RealtimeSignalSnapshot:
    user_id: str
    calculated_at: datetime
    latest_glucose_mg_dl: float | None
    latest_measured_at: datetime | None
    latest_received_at: datetime | None
    data_freshness_minutes: float | None
    collector_lag_minutes: float | None
    delta_15min: float | None
    delta_30min: float | None
    slope_15min_mg_dl_per_min: float | None
    rolling_mean_1h: float | None
    missing_rate_1h: float
    stale_status: bool
    point_count_1h: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "calculated_at": self.calculated_at.isoformat(),
            "latest_glucose_mg_dl": self.latest_glucose_mg_dl,
            "latest_measured_at": self.latest_measured_at.isoformat() if self.latest_measured_at else None,
            "latest_received_at": self.latest_received_at.isoformat() if self.latest_received_at else None,
            "data_freshness_minutes": self.data_freshness_minutes,
            "collector_lag_minutes": self.collector_lag_minutes,
            "delta_15min": self.delta_15min,
            "delta_30min": self.delta_30min,
            "slope_15min_mg_dl_per_min": self.slope_15min_mg_dl_per_min,
            "rolling_mean_1h": self.rolling_mean_1h,
            "missing_rate_1h": self.missing_rate_1h,
            "stale_status": self.stale_status,
            "point_count_1h": self.point_count_1h,
        }


@dataclass(frozen=True)
class RealtimeSignalConfig:
    expected_interval_minutes: int = 5
    stale_after_minutes: int = 10


class RealtimeSignalService:
    def __init__(self, config: RealtimeSignalConfig | None = None) -> None:
        self.config = config or RealtimeSignalConfig()

    def compute(
        self,
        *,
        points: list[GlucosePoint],
        scope: DataScope,
        now: datetime | None = None,
    ) -> RealtimeSignalSnapshot:
        calculated_at = _as_utc(now or utc_now())
        eligible = sorted(
            [
                point
                for point in points
                if point.user_id == scope.user_id
                and scope.window_start <= point.timestamp < scope.window_end
                and (scope.source is None or point.source == scope.source)
                and str(point.quality_flag) == "valid"
            ],
            key=lambda point: point.timestamp,
        )
        one_hour_start = calculated_at - timedelta(hours=1)
        hour_points = [point for point in eligible if point.timestamp >= one_hour_start]
        expected_1h = max(1, math.ceil(60 / self.config.expected_interval_minutes))
        missing_rate = max(0.0, round((1 - (len(hour_points) / expected_1h)) * 100, 2))

        if not eligible:
            return RealtimeSignalSnapshot(
                user_id=scope.user_id,
                calculated_at=calculated_at,
                latest_glucose_mg_dl=None,
                latest_measured_at=None,
                latest_received_at=None,
                data_freshness_minutes=None,
                collector_lag_minutes=None,
                delta_15min=None,
                delta_30min=None,
                slope_15min_mg_dl_per_min=None,
                rolling_mean_1h=None,
                missing_rate_1h=missing_rate,
                stale_status=True,
                point_count_1h=0,
            )

        latest = eligible[-1]
        freshness = _minutes_between(latest.timestamp, calculated_at)
        lag = (
            _minutes_between(latest.timestamp, latest.received_at)
            if latest.received_at is not None
            else None
        )
        delta_15 = _delta_since(eligible, latest, minutes=15)
        delta_30 = _delta_since(eligible, latest, minutes=30)
        rolling_mean = (
            round(sum(point.value_mg_dl for point in hour_points) / len(hour_points), 2)
            if hour_points
            else None
        )
        return RealtimeSignalSnapshot(
            user_id=scope.user_id,
            calculated_at=calculated_at,
            latest_glucose_mg_dl=latest.value_mg_dl,
            latest_measured_at=latest.timestamp,
            latest_received_at=latest.received_at,
            data_freshness_minutes=freshness,
            collector_lag_minutes=lag,
            delta_15min=delta_15,
            delta_30min=delta_30,
            slope_15min_mg_dl_per_min=(
                round(delta_15 / 15, 4) if delta_15 is not None else None
            ),
            rolling_mean_1h=rolling_mean,
            missing_rate_1h=missing_rate,
            stale_status=freshness > self.config.stale_after_minutes,
            point_count_1h=len(hour_points),
        )


def _delta_since(points: list[GlucosePoint], latest: GlucosePoint, *, minutes: int) -> float | None:
    target = latest.timestamp - timedelta(minutes=minutes)
    candidates = [point for point in points if point.timestamp <= target]
    if not candidates:
        return None
    baseline = max(candidates, key=lambda point: point.timestamp)
    return round(latest.value_mg_dl - baseline.value_mg_dl, 2)


def _minutes_between(start: datetime, end: datetime | None) -> float | None:
    if end is None:
        return None
    return round((_as_utc(end) - _as_utc(start)).total_seconds() / 60, 2)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)
