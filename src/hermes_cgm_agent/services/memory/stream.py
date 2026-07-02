from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.domain import DataScope, GlucoseEvent, L1Episode
from hermes_cgm_agent.services.analytics import AnalyticsConfig, CGMAnalyticsService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.consolidation import ConsolidationReport, ConsolidationService
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository


@dataclass(frozen=True)
class StreamMemoryConfig:
    expected_interval_minutes: int = 5
    warm_period: str = "daily"
    warm_span_days: int = 1
    warm_refresh_min_interval_minutes: int = 60
    event_episode_confidence: float = 0.9


@dataclass(frozen=True)
class StreamMemoryResult:
    l1_episodes_created: int = 0
    l1_episode_duplicates: int = 0
    warm_summary_created: bool = False
    consolidation: ConsolidationReport = ConsolidationReport()


class StreamMemoryService:
    """Persist source-poll derived CGM facts into the long-term memory stack.

    Raw glucose points stay in the time-series store. Durable L1 memory is only
    created for deterministic glucose events, because those are bounded,
    evidence-backed facts. Warm summaries are refreshed from recent aggregates so
    Hermes prefetch has a compact current state even when no event occurred.
    """

    def __init__(
        self,
        *,
        cgm_repository: SQLiteCGMRepository,
        memory_repository: SQLiteMemoryRepository,
        consolidation: ConsolidationService | None = None,
        analytics: CGMAnalyticsService | None = None,
        config: StreamMemoryConfig | None = None,
    ) -> None:
        self.cgm_repository = cgm_repository
        self.memory_repository = memory_repository
        self.config = config or StreamMemoryConfig()
        self.consolidation = consolidation or ConsolidationService(repository=memory_repository)
        self.analytics = analytics or CGMAnalyticsService(
            AnalyticsConfig(expected_interval_minutes=self.config.expected_interval_minutes)
        )

    def ingest_poll_result(
        self,
        *,
        user_id: str,
        source: str,
        reading_times: list[datetime],
        inserted_point_count: int,
        inserted_events: list[GlucoseEvent],
        now: datetime,
    ) -> StreamMemoryResult:
        created = 0
        duplicates = 0
        for event in inserted_events:
            episode = self._episode_from_event(event, now=now)
            try:
                self.memory_repository.create_episode(episode)
                created += 1
            except sqlite3.IntegrityError:
                duplicates += 1

        consolidation = self.consolidation.consolidate(
            user_id,
            now=now,
            session_id="source-poll-memory",
        )
        warm_created = self._maybe_synthesize_warm_summary(
            user_id=user_id,
            source=source,
            reading_times=reading_times,
            inserted_point_count=inserted_point_count,
            inserted_event_count=created,
            now=now,
        )
        return StreamMemoryResult(
            l1_episodes_created=created,
            l1_episode_duplicates=duplicates,
            warm_summary_created=warm_created,
            consolidation=consolidation,
        )

    def _episode_from_event(self, event: GlucoseEvent, *, now: datetime) -> L1Episode:
        event_type = str(event.event_type)
        return L1Episode(
            episode_id=f"evt-{event.event_id}",
            user_id=event.user_id,
            occurred_at=event.ts_start,
            episode_type=event_type,
            summary=event.summary,
            payload={
                "event_id": event.event_id,
                "event_type": event_type,
                "ts_start": event.ts_start.isoformat(),
                "ts_end": event.ts_end.isoformat(),
                "severity": str(event.severity),
                "duration_minutes": event.duration_minutes,
                "point_count": event.point_count,
            },
            evidence_refs=event.evidence_refs,
            confidence=self.config.event_episode_confidence,
            created_at=now,
            last_referenced_at=now,
        )

    def _maybe_synthesize_warm_summary(
        self,
        *,
        user_id: str,
        source: str,
        reading_times: list[datetime],
        inserted_point_count: int,
        inserted_event_count: int,
        now: datetime,
    ) -> bool:
        if inserted_point_count <= 0 and inserted_event_count <= 0:
            return False
        latest = self.memory_repository.latest_summary(user_id, period=self.config.warm_period)
        if latest is not None and inserted_event_count <= 0:
            cutoff = _as_utc(now) - timedelta(
                minutes=self.config.warm_refresh_min_interval_minutes
            )
            if _as_utc(latest.created_at) > cutoff:
                return False

        anchor_candidates = [_as_utc(item) for item in reading_times]
        anchor_candidates.append(_as_utc(now))
        window_end = max(anchor_candidates) + timedelta(
            minutes=self.config.expected_interval_minutes
        )
        window_start = window_end - timedelta(days=self.config.warm_span_days)
        scope = DataScope(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
            source=source,
        )
        aggregate = self.analytics.compute_aggregate(
            points=self.cgm_repository.list_glucose_points(scope),
            scope=scope,
            window_label="day" if self.config.warm_period == "daily" else None,
        )
        self.consolidation.synthesize_state(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
            period=self.config.warm_period,
            metrics_summary={
                "tir_pct": aggregate.tir,
                "mean_mgdl": aggregate.mbg,
                "point_count": aggregate.point_count,
                "data_coverage_pct": aggregate.data_coverage,
            },
            now=now,
        )
        return True


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)
