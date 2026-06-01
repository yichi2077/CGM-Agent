"""L0 Context Builder (MEM-ARCH-20260601 §4; DECISION_LOG D024/D027).

Assembles the L0 working-memory context for a 14-day sensor cycle as a
deterministically compressed, token-bounded STRUCTURED object. Metrics come
from analytics (D015); the LLM never sees raw points as the memory mechanism.

Compression "near_point_far_hourly_v1":
- near 3 days  -> point-level
- days 4-7     -> hourly summaries
- days 8-span  -> daily aggregates only
- detected glucose events + confirmed user events -> always kept as anchors

source_mode is "recompute" (D027): summaries are computed on read. The contract
leaves room for a future "materialized" mode without changing L0Context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import (
    DataScope,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    L0Context,
    L0DailyAggregate,
    L0HourlySummary,
    L0Window,
    L0_COMPRESSION_POLICY,
    L0_DEFAULT_SPAN_DAYS,
    L0_DEFAULT_TOKEN_BUDGET,
)
from hermes_cgm_agent.domain.report import DataQualityWarning, DataQualitySeverity
from hermes_cgm_agent.services.analytics import (
    CGMAnalyticsService,
    GlucoseEventDetector,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository


@dataclass(frozen=True)
class L0ContextConfig:
    span_days: int = L0_DEFAULT_SPAN_DAYS
    near_point_days: int = 3
    mid_hourly_days: int = 7
    token_budget: int = L0_DEFAULT_TOKEN_BUDGET
    timezone: str = "Asia/Shanghai"
    # Coarse token estimate weights (tokens per item). Deterministic, not exact.
    tokens_per_point: int = 12
    tokens_per_hourly: int = 18
    tokens_per_daily: int = 30
    tokens_per_event: int = 40


class L0ContextBuilder:
    def __init__(
        self,
        *,
        cgm_repository: SQLiteCGMRepository,
        config: L0ContextConfig | None = None,
        analytics_service: CGMAnalyticsService | None = None,
        event_detector: GlucoseEventDetector | None = None,
    ) -> None:
        self.cgm_repository = cgm_repository
        self.config = config or L0ContextConfig()
        self.analytics_service = analytics_service or CGMAnalyticsService()
        self.event_detector = event_detector or GlucoseEventDetector()

    def build(
        self,
        *,
        user_id: str,
        anchor_at: datetime | None = None,
        anchor_time: time = time(7, 0),
    ) -> L0Context:
        window = self._resolve_window(user_id=user_id, anchor_at=anchor_at, anchor_time=anchor_time)
        scope = DataScope(
            user_id=user_id,
            window_start=window.window_start,
            window_end=window.window_end,
        )
        points = sorted(
            self.cgm_repository.list_glucose_points(scope),
            key=lambda point: point.timestamp,
        )
        confirmed_events = self.cgm_repository.list_user_events(scope, confirmed_only=True)
        detected_events = self.event_detector.detect(points=points, scope=scope)

        window_summary = self.analytics_service.compute_aggregate(
            points=points, scope=scope, window_label="14d"
        )
        daily = self._daily_aggregates(points=points, window=window)

        near_cutoff = window.window_end - timedelta(days=self.config.near_point_days)
        mid_cutoff = window.window_end - timedelta(days=self.config.mid_hourly_days)

        high_res_recent = [p for p in points if p.timestamp >= near_cutoff]
        mid_points = [p for p in points if mid_cutoff <= p.timestamp < near_cutoff]
        mid_far_hourly = self._hourly_summaries(mid_points, detected_events)
        far_daily_only = [d for d in daily if d.day < (window.window_end - timedelta(days=self.config.mid_hourly_days)).date()]

        data_quality = self._data_quality(points=points, aggregate=window_summary)

        context = L0Context(
            window=window,
            window_summary=window_summary,
            daily_aggregates=daily,
            high_res_recent=high_res_recent,
            mid_far_hourly=mid_far_hourly,
            far_daily_only=far_daily_only,
            key_glucose_events=detected_events,
            confirmed_user_events=confirmed_events,
            data_quality=data_quality,
            token_budget=self.config.token_budget,
            compression_policy=L0_COMPRESSION_POLICY,
            source_mode="recompute",
        )
        context.estimated_tokens = self._estimate_tokens(context)
        return self._enforce_budget(context)

    def _resolve_window(
        self,
        *,
        user_id: str,
        anchor_at: datetime | None,
        anchor_time: time,
    ) -> L0Window:
        zone = ZoneInfo(self.config.timezone)
        now = (anchor_at or datetime.now(timezone.utc)).astimezone(zone)
        local_anchor = now.replace(
            hour=anchor_time.hour,
            minute=anchor_time.minute,
            second=0,
            microsecond=0,
        )
        if now < local_anchor:
            local_anchor = local_anchor - timedelta(days=1)
        window_end = local_anchor.astimezone(timezone.utc)
        window_start = window_end - timedelta(days=self.config.span_days)
        return L0Window(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
            span_days=self.config.span_days,
            timezone=self.config.timezone,
        )

    def _daily_aggregates(
        self,
        *,
        points: list[GlucosePoint],
        window: L0Window,
    ) -> list[L0DailyAggregate]:
        zone = ZoneInfo(self.config.timezone)
        result: list[L0DailyAggregate] = []
        day_start = window.window_start
        while day_start < window.window_end:
            day_end = min(day_start + timedelta(days=1), window.window_end)
            day_scope = DataScope(
                user_id=window.user_id,
                window_start=day_start,
                window_end=day_end,
            )
            day_points = [p for p in points if day_start <= p.timestamp < day_end]
            aggregate = self.analytics_service.compute_aggregate(
                points=day_points, scope=day_scope, window_label="day"
            )
            local_day = day_start.astimezone(zone).date()
            result.append(L0DailyAggregate(day=local_day, aggregate=aggregate))
            day_start = day_end
        return result

    def _hourly_summaries(
        self,
        points: list[GlucosePoint],
        detected_events: list[GlucoseEvent],
    ) -> list[L0HourlySummary]:
        buckets: dict[datetime, list[float]] = {}
        for point in points:
            hour = point.timestamp.replace(minute=0, second=0, microsecond=0)
            buckets.setdefault(hour, []).append(point.value_mg_dl)
        event_hours = {
            event.ts_start.replace(minute=0, second=0, microsecond=0)
            for event in detected_events
        }
        summaries: list[L0HourlySummary] = []
        for hour in sorted(buckets):
            values = buckets[hour]
            summaries.append(
                L0HourlySummary(
                    hour_start=hour,
                    mean_mg_dl=round(sum(values) / len(values), 2),
                    min_mg_dl=min(values),
                    max_mg_dl=max(values),
                    point_count=len(values),
                    has_event=hour in event_hours,
                )
            )
        return summaries

    def _data_quality(
        self,
        *,
        points: list[GlucosePoint],
        aggregate: GlucoseAggregate,
    ) -> list[DataQualityWarning]:
        warnings: list[DataQualityWarning] = []
        if aggregate.point_count == 0:
            warnings.append(
                DataQualityWarning(
                    code="no_valid_points",
                    message="No valid CGM points in the L0 window.",
                    severity=DataQualitySeverity.WARNING,
                )
            )
        elif aggregate.data_coverage < 70:
            warnings.append(
                DataQualityWarning(
                    code="low_coverage",
                    message=f"L0 window coverage is {aggregate.data_coverage}%, below the 70% review threshold.",
                    severity=DataQualitySeverity.WARNING,
                )
            )
        return warnings

    def _estimate_tokens(self, context: L0Context) -> int:
        cfg = self.config
        return (
            len(context.high_res_recent) * cfg.tokens_per_point
            + len(context.mid_far_hourly) * cfg.tokens_per_hourly
            + (len(context.daily_aggregates) + len(context.far_daily_only)) * cfg.tokens_per_daily
            + (len(context.key_glucose_events) + len(context.confirmed_user_events))
            * cfg.tokens_per_event
        )

    def _enforce_budget(self, context: L0Context) -> L0Context:
        """Degrade far->near if over budget, but never drop event anchors or
        daily/window aggregates (metrics are the factual floor, §4.3)."""
        if context.estimated_tokens <= context.token_budget:
            return context
        # Step 1: drop mid-far hourly detail (days 4-7 fall back to daily aggregates).
        context.mid_far_hourly = []
        context.estimated_tokens = self._estimate_tokens(context)
        if context.estimated_tokens <= context.token_budget:
            return context
        # Step 2: thin near-window points to hourly cadence (keep one per ~12 points).
        if context.high_res_recent:
            context.high_res_recent = context.high_res_recent[::12] or context.high_res_recent[:1]
            context.estimated_tokens = self._estimate_tokens(context)
        return context
