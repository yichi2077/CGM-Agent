"""L0 working-memory context object (MEM-ARCH-20260601 §4).

L0 is NOT raw data injected into a long-context LLM (DECISION_LOG D024). It is a
deterministically compressed, token-bounded STRUCTURED object assembled by the
Context Builder. The long-context window is only the delivery vehicle; metrics
always come from analytics (D015), never from the LLM.

Compression policy "near_point_far_hourly_v1" (§4.3):
- near 3 days  -> point-level (day-sliding)
- days 4-7     -> hourly summaries
- days 8-span  -> daily aggregates only
- detected/confirmed events are always kept in full as explicit anchors.

Span is fixed at 14 days (one sensor wear cycle = natural hardware boundary,
D027). Long-term memory is carried by L1/L2/L3, not by widening L0.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, model_validator

from hermes_cgm_agent.domain.cgm import (
    CGMBaseModel,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    UserEvent,
    utc_now,
)
from hermes_cgm_agent.domain.report import DataQualityWarning

L0_DEFAULT_SPAN_DAYS = 14
L0_NEAR_POINT_DAYS = 3
L0_MID_HOURLY_DAYS = 7
L0_COMPRESSION_POLICY = "near_point_far_hourly_v1"
L0_DEFAULT_TOKEN_BUDGET = 60_000


class L0Window(CGMBaseModel):
    user_id: str
    window_start: datetime
    window_end: datetime
    span_days: int = L0_DEFAULT_SPAN_DAYS
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_window(self) -> L0Window:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class L0HourlySummary(CGMBaseModel):
    hour_start: datetime
    mean_mg_dl: float | None = None
    min_mg_dl: float | None = None
    max_mg_dl: float | None = None
    point_count: int = Field(default=0, ge=0)
    has_event: bool = False


class L0DailyAggregate(CGMBaseModel):
    day: date
    aggregate: GlucoseAggregate


class L0Context(CGMBaseModel):
    window: L0Window
    window_summary: GlucoseAggregate
    daily_aggregates: list[L0DailyAggregate] = Field(default_factory=list)
    high_res_recent: list[GlucosePoint] = Field(default_factory=list)
    mid_far_hourly: list[L0HourlySummary] = Field(default_factory=list)
    far_daily_only: list[L0DailyAggregate] = Field(default_factory=list)
    key_glucose_events: list[GlucoseEvent] = Field(default_factory=list)
    confirmed_user_events: list[UserEvent] = Field(default_factory=list)
    data_quality: list[DataQualityWarning] = Field(default_factory=list)
    token_budget: int = Field(default=L0_DEFAULT_TOKEN_BUDGET, ge=0)
    estimated_tokens: int = Field(default=0, ge=0)
    compression_policy: str = L0_COMPRESSION_POLICY
    source_mode: str = "recompute"
    built_at: datetime = Field(default_factory=utc_now)
