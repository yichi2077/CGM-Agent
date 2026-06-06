from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import (
    DataScope,
    L0Context,
    L0DailyAggregate,
    L0HourlySummary,
    L0Window,
)
from hermes_cgm_agent.domain.context import (
    L0_DEFAULT_SPAN_DAYS,
    L0_DEFAULT_TOKEN_BUDGET,
    L0_MID_HOURLY_DAYS,
    L0_NEAR_POINT_DAYS,
)
from hermes_cgm_agent.domain.report import DataQualityWarning
from hermes_cgm_agent.services.analytics import CGMAnalyticsService, GlucoseEventDetector
from hermes_cgm_agent.services.data import SQLiteCGMRepository


@dataclass(frozen=True)
class L0BuildConfig:
    span_days: int = L0_DEFAULT_SPAN_DAYS
    timezone: str = "Asia/Shanghai"
    token_budget: int = L0_DEFAULT_TOKEN_BUDGET


class L0ContextBuilder:
    """Build deterministic short-term working memory from local CGM data (D038)."""

    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        analytics_service: CGMAnalyticsService | None = None,
        event_detector: GlucoseEventDetector | None = None,
        config: L0BuildConfig | None = None,
    ) -> None:
        self.repository = repository
        self.analytics_service = analytics_service or CGMAnalyticsService()
        self.event_detector = event_detector or GlucoseEventDetector()
        self.config = config or L0BuildConfig()

    def build(
        self,
        *,
        user_id: str,
        anchor_at: datetime | None = None,
        source: str | None = None,
    ) -> L0Context:
        window_end = _as_utc(anchor_at or datetime.now(timezone.utc))
        window_start = window_end - timedelta(days=self.config.span_days)
        scope = DataScope(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
            source=source,
        )
        points = self.repository.list_glucose_points(scope)
        aggregate = self.analytics_service.compute_aggregate(
            points=points,
            scope=scope,
            window_label=f"{self.config.span_days}d",
        )
        detected_events = self.event_detector.detect(points=points, scope=scope)
        confirmed_events = self.repository.list_user_events(scope, confirmed_only=True)
        daily = self._daily_aggregates(points=points, scope=scope)
        context = L0Context(
            window=L0Window(
                user_id=user_id,
                window_start=window_start,
                window_end=window_end,
                span_days=self.config.span_days,
                timezone=self.config.timezone,
            ),
            window_summary=aggregate,
            daily_aggregates=daily,
            high_res_recent=[
                point
                for point in points
                if point.timestamp >= window_end - timedelta(days=L0_NEAR_POINT_DAYS)
            ],
            mid_far_hourly=self._hourly_summaries(
                points=[
                    point
                    for point in points
                    if window_end - timedelta(days=L0_MID_HOURLY_DAYS)
                    <= point.timestamp
                    < window_end - timedelta(days=L0_NEAR_POINT_DAYS)
                ],
                events=detected_events,
            ),
            far_daily_only=[
                item
                for item in daily
                if _day_end_utc(item.day, self.config.timezone)
                <= window_end - timedelta(days=L0_MID_HOURLY_DAYS)
            ],
            key_glucose_events=detected_events,
            confirmed_user_events=confirmed_events,
            data_quality=_data_quality(points),
            token_budget=self.config.token_budget,
            estimated_tokens=0,
        )
        return self._fit_budget(context)

    def _daily_aggregates(
        self,
        *,
        points: list,
        scope: DataScope,
    ) -> list[L0DailyAggregate]:
        zone = ZoneInfo(self.config.timezone)
        by_day: dict = defaultdict(list)
        for point in points:
            by_day[point.timestamp.astimezone(zone).date()].append(point)
        out: list[L0DailyAggregate] = []
        for day in sorted(by_day):
            day_points = by_day[day]
            day_scope = DataScope(
                user_id=scope.user_id,
                window_start=min(point.timestamp for point in day_points),
                window_end=max(point.timestamp for point in day_points) + timedelta(microseconds=1),
                source=scope.source,
            )
            out.append(
                L0DailyAggregate(
                    day=day,
                    aggregate=self.analytics_service.compute_aggregate(
                        points=day_points,
                        scope=day_scope,
                        window_label="day",
                    ),
                )
            )
        return out

    @staticmethod
    def _hourly_summaries(*, points: list, events: list) -> list[L0HourlySummary]:
        by_hour: dict[datetime, list] = defaultdict(list)
        for point in points:
            hour = point.timestamp.replace(minute=0, second=0, microsecond=0)
            by_hour[hour].append(point)
        summaries: list[L0HourlySummary] = []
        for hour in sorted(by_hour):
            values = [point.value_mg_dl for point in by_hour[hour]]
            summaries.append(
                L0HourlySummary(
                    hour_start=hour,
                    mean_mg_dl=round(sum(values) / len(values), 2) if values else None,
                    min_mg_dl=min(values) if values else None,
                    max_mg_dl=max(values) if values else None,
                    point_count=len(values),
                    has_event=any(
                        event.ts_start < hour + timedelta(hours=1)
                        and (event.ts_end or event.ts_start) >= hour
                        for event in events
                    ),
                )
            )
        return summaries

    def _fit_budget(self, context: L0Context) -> L0Context:
        estimated = _estimate_tokens(context)
        if estimated <= context.token_budget:
            return context.model_copy(update={"estimated_tokens": estimated})
        recent = list(context.high_res_recent)
        while recent and estimated > context.token_budget:
            recent.pop(0)
            estimated = _estimate_tokens(context.model_copy(update={"high_res_recent": recent}))
        return context.model_copy(
            update={"high_res_recent": recent, "estimated_tokens": estimated}
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _data_quality(points: list) -> list[DataQualityWarning]:
    if points:
        return []
    return [
        DataQualityWarning(
            code="no_valid_points",
            message="No valid glucose points were found in the L0 window.",
        )
    ]


def _estimate_tokens(context: L0Context) -> int:
    return (
        120
        + len(context.high_res_recent) * 24
        + len(context.mid_far_hourly) * 16
        + len(context.daily_aggregates) * 32
        + len(context.key_glucose_events) * 48
        + len(context.confirmed_user_events) * 48
        + len(context.data_quality) * 24
    )


def _day_end_utc(day, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(
        timezone.utc
    )
