from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_cgm_agent.config import default_timezone
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
from hermes_cgm_agent.services.analytics import (
    AnalyticsConfig,
    CGMAnalyticsService,
    EventDetectionConfig,
    GlucoseEventDetector,
    median_interval_minutes,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository


@dataclass(frozen=True)
class L0BuildConfig:
    span_days: int = L0_DEFAULT_SPAN_DAYS
    timezone: str = field(default_factory=default_timezone)
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
        self.config = config or L0BuildConfig()
        # Cadence-adaptive defaults (D053): when no explicit services are
        # injected, build() re-tunes them to the device's observed sampling
        # interval so gap detection and coverage stay correct on 1-minute
        # AiDEX-style feeds. Injected services are always respected as-is.
        self._services_injected = analytics_service is not None or event_detector is not None
        self.analytics_service = analytics_service or CGMAnalyticsService()
        self.event_detector = event_detector or GlucoseEventDetector(
            EventDetectionConfig(timezone=self.config.timezone)
        )

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
        # M-06: use local variables for cadence-adapted services instead of
        # mutating self, so build() stays side-effect-free and re-entrant.
        analytics_service = self.analytics_service
        event_detector = self.event_detector
        if not self._services_injected and points:
            interval = median_interval_minutes([point.timestamp for point in points])
            analytics_service = CGMAnalyticsService(
                AnalyticsConfig(expected_interval_minutes=interval)
            )
            event_detector = GlucoseEventDetector(
                EventDetectionConfig(
                    expected_interval_minutes=interval,
                    timezone=self.config.timezone,
                )
            )
        aggregate = analytics_service.compute_aggregate(
            points=points,
            scope=scope,
            window_label=f"{self.config.span_days}d",
        )
        detected_events = event_detector.detect(points=points, scope=scope)
        confirmed_events = self.repository.list_user_events(scope, confirmed_only=True)
        daily = self._daily_aggregates(
            points=points, scope=scope, analytics_service=analytics_service
        )
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
                timezone_name=self.config.timezone,
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
        analytics_service: CGMAnalyticsService | None = None,
    ) -> list[L0DailyAggregate]:
        # M-06: accept the (possibly cadence-adapted) analytics_service from
        # build() instead of reading self.analytics_service, so build() can
        # stay side-effect-free.
        svc = analytics_service or self.analytics_service
        zone = ZoneInfo(self.config.timezone)
        by_day: dict = defaultdict(list)
        for point in points:
            by_day[point.timestamp.astimezone(zone).date()].append(point)
        out: list[L0DailyAggregate] = []
        for day in sorted(by_day):
            day_points = by_day[day]
            day_scope = DataScope(
                user_id=scope.user_id,
                window_start=datetime.combine(day, datetime.min.time()).replace(
                    tzinfo=zone
                ),
                window_end=datetime.combine(
                    day + timedelta(days=1), datetime.min.time()
                ).replace(tzinfo=zone),
                source=scope.source,
            )
            out.append(
                L0DailyAggregate(
                    day=day,
                    aggregate=svc.compute_aggregate(
                        points=day_points,
                        scope=day_scope,
                        window_label="day",
                    ),
                )
            )
        return out

    @staticmethod
    def _hourly_summaries(*, points: list, events: list, timezone_name: str) -> list[L0HourlySummary]:
        # H-08: group by LOCAL hour, not UTC hour.  Without this, a user in
        # UTC+8 with a 09:30 reading gets bucketed into the 01:00 hour.
        zone = ZoneInfo(timezone_name)
        by_hour: dict[datetime, list] = defaultdict(list)
        for point in points:
            local_ts = point.timestamp.astimezone(zone)
            hour = local_ts.replace(minute=0, second=0, microsecond=0)
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
