from __future__ import annotations

import sqlite3
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.domain.report import ReportInput
from hermes_cgm_agent.services.analytics import (
    AnalyticsConfig,
    CGMAnalyticsService,
    EventDetectionConfig,
    GlucoseEventDetector,
)
from hermes_cgm_agent.services.memory import ConsolidationService, SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.derive import episodes_from_detected_events
from hermes_cgm_agent.services.memory.l0_builder import L0ContextBuilder, L0BuildConfig
from hermes_cgm_agent.services.reports.builder import ReportService
from hermes_cgm_agent.services.reports.repository import SQLiteReportRepository
from hermes_cgm_agent.services.scheduling import PushSchedulerConfig, PushSchedulerService
from hermes_cgm_agent.services.simulation.audit import SimulationAudit
from hermes_cgm_agent.services.simulation.clock import SimClock
from hermes_cgm_agent.services.simulation.ingest import StreamIngestor
from hermes_cgm_agent.services.simulation.source import CsvReplaySource
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class SimulationRunResult:
    status: str
    exit_code: int
    run_id: str
    out_dir: Path
    db_path: Path
    emitted: int
    inserted: int
    duplicate: int
    issues: int
    report_json: Path
    report_md: Path
    stage_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "run_id": self.run_id,
            "out_dir": str(self.out_dir),
            "db_path": str(self.db_path),
            "emitted": self.emitted,
            "inserted": self.inserted,
            "duplicate": self.duplicate,
            "issues": self.issues,
            "report_json": str(self.report_json),
            "report_md": str(self.report_md),
            "stage_counts": self.stage_counts,
        }


class SimulationRunner:
    def __init__(
        self,
        *,
        db_path: Path,
        out_dir: Path,
        user_id: str,
        source_label: str = "simulation:csv",
        timezone_name: str | None = None,
        acceleration: float = 300.0,
        max_speed: bool = False,
        expected_interval_minutes: int | None = None,
    ) -> None:
        if timezone_name is None:
            timezone_name = default_timezone()
        self.db_path = db_path
        self.out_dir = out_dir
        self.user_id = user_id
        self.source_label = source_label
        self.timezone_name = timezone_name
        self.acceleration = acceleration
        self.max_speed = max_speed
        # None -> infer from the replayed data's median cadence (a 1-minute
        # AiDEX-style feed must not be measured against the 5-minute default).
        self.expected_interval_minutes = expected_interval_minutes

    def run(self, source: CsvReplaySource, *, fail_fast: bool = False) -> SimulationRunResult:
        run_id = uuid.uuid4().hex[:12]
        store = SQLiteStore(self.db_path)
        store.initialize()
        cgm_repository = SQLiteCGMRepository(store)
        memory_repository = SQLiteMemoryRepository(store)
        ingest = StreamIngestor(
            repository=cgm_repository,
            user_id=self.user_id,
            source=self.source_label,
            default_timezone=self.timezone_name,
        )
        audit = SimulationAudit(run_id=run_id, out_dir=self.out_dir)
        audit.record("run_start", db_path=str(self.db_path), csv=str(source.path))
        ingest.archive_batch(source.batch)
        records = list(source.iter_records())
        if not records:
            audit.issue(stage="source", message="source emitted no records")
            json_path, md_path = audit.write()
            return self._result("failed", 1, run_id, audit, 0, json_path, md_path)

        replay_scope = DataScope(
            user_id=self.user_id,
            window_start=records[0].sim_ts,
            window_end=records[-1].sim_ts + timedelta(minutes=5),
            source=self.source_label,
        )
        preexisting_count = len(cgm_repository.list_glucose_points(replay_scope))
        pipeline_counts_before = _pipeline_counts(
            memory_repository,
            SQLiteReportRepository(store),
            self.user_id,
        )

        clock = SimClock(
            start=records[0].sim_ts,
            acceleration=self.acceleration,
            max_speed=self.max_speed,
        )
        interval_minutes = self.expected_interval_minutes or _infer_interval_minutes(records)
        audit.set_invariant("expected_interval_minutes", interval_minutes)
        analytics = CGMAnalyticsService(
            AnalyticsConfig(expected_interval_minutes=interval_minutes)
        )
        detector = GlucoseEventDetector(
            EventDetectionConfig(expected_interval_minutes=interval_minutes)
        )
        scheduler = PushSchedulerService(
            store=store,
            config=PushSchedulerConfig(timezone=self.timezone_name),
        )
        consolidation = ConsolidationService(repository=memory_repository)
        reporter = ReportService(
            cgm_repository=cgm_repository,
            report_repository=SQLiteReportRepository(store),
        )
        next_hour = _ceil_hour(records[0].sim_ts)
        seen_daily_push: set[str] = set()
        seen_daily_memory: set[str] = set()
        stage_counts: dict[str, int] = {}
        push_idempotency_checked = False
        inserted_this_run = 0

        for item in records:
            try:
                sim_now = clock.advance_to(item.sim_ts)
                ingest_result = ingest.ingest_record(
                    item.record,
                    batch_id=source.batch.batch_id,
                )
                audit.record(
                    "ingest",
                    sim_now=sim_now.isoformat(),
                    reading_index=item.reading_index,
                    inserted=ingest_result.inserted,
                    duplicate=ingest_result.duplicate,
                    issues=ingest_result.issues,
                )
                stage_counts["ingest"] = stage_counts.get("ingest", 0) + 1
                inserted_this_run += ingest_result.inserted

                # A process restart may replay an already-accounted prefix (or
                # the whole file). Those facts are still audited as duplicates,
                # but must not re-trigger downstream reports, memory summaries,
                # or scheduled pushes.
                if ingest_result.inserted == 0:
                    continue

                while sim_now >= next_hour:
                    self._hourly(cgm_repository, analytics, detector, audit, next_hour)
                    stage_counts["hourly"] = stage_counts.get("hourly", 0) + 1
                    next_hour += timedelta(hours=1)

                local_now = sim_now.astimezone(ZoneInfo(self.timezone_name))
                day_key = local_now.strftime("%Y-%m-%d")
                if local_now.hour >= 9 and day_key not in seen_daily_push:
                    first_push = scheduler.push_tick(user_id=self.user_id, now=sim_now)
                    push_correlation = f"push:{day_key}"
                    audit.record(
                        "push",
                        correlation_id=push_correlation,
                        sim_now=sim_now.isoformat(),
                        day=day_key,
                        pushed_count=len(first_push.pushed),
                    )
                    audit.link(
                        from_stage="ingest_day",
                        from_id=day_key,
                        to_stage="push",
                        to_id=push_correlation,
                        relation="scheduled_from",
                    )
                    # Idempotency probe (once): re-tick at the same sim time and
                    # assert the scheduler does not re-emit an already-pushed
                    # period. Only meaningful when the first tick actually pushed.
                    if not push_idempotency_checked and first_push.pushed:
                        repeat_push = scheduler.push_tick(user_id=self.user_id, now=sim_now)
                        audit.set_invariant(
                            "push_idempotent", len(repeat_push.pushed) == 0
                        )
                        if repeat_push.pushed:
                            audit.issue(
                                stage="push",
                                sim_now=sim_now,
                                reading_index=item.reading_index,
                                message="push_tick re-emitted an already-pushed period at the same sim time",
                            )
                        push_idempotency_checked = True
                    seen_daily_push.add(day_key)
                    stage_counts["push"] = stage_counts.get("push", 0) + 1

                if local_now.time() >= time(23, 55) and day_key not in seen_daily_memory:
                    memory_correlation = f"memory:{day_key}"
                    counts_before = _pipeline_counts(
                        memory_repository, SQLiteReportRepository(store), self.user_id
                    )
                    self._daily_memory(
                        cgm_repository,
                        memory_repository,
                        consolidation,
                        analytics,
                        detector,
                        sim_now,
                    )
                    counts_after = _pipeline_counts(
                        memory_repository, SQLiteReportRepository(store), self.user_id
                    )
                    audit.record(
                        "memory",
                        correlation_id=memory_correlation,
                        sim_now=sim_now.isoformat(),
                        window_start=(sim_now - timedelta(days=1)).isoformat(),
                        window_end=sim_now.isoformat(),
                        counts_before=counts_before,
                        counts_after=counts_after,
                    )
                    audit.link(
                        from_stage="ingest_day",
                        from_id=day_key,
                        to_stage="memory",
                        to_id=memory_correlation,
                        relation="derived_from",
                    )
                    seen_daily_memory.add(day_key)
                    stage_counts["memory"] = stage_counts.get("memory", 0) + 1

                    self._build_l0(
                        cgm_repository, analytics, detector,
                        sim_now, audit, day_key, stage_counts,
                    )

                    generated_report = reporter.generate(
                        ReportInput(
                            report_type="daily",
                            user_id=self.user_id,
                            audience="self",
                            anchor_at=sim_now,
                            timezone=self.timezone_name,
                        )
                    )
                    audit.record(
                        "report",
                        correlation_id=f"report:{generated_report.report_id}",
                        parent_correlation_id=memory_correlation,
                        sim_now=sim_now.isoformat(),
                        report_id=generated_report.report_id,
                        report_type="daily",
                    )
                    audit.link(
                        from_stage="memory",
                        from_id=memory_correlation,
                        to_stage="report",
                        to_id=generated_report.report_id,
                        relation="informed",
                    )
                    stage_counts["report"] = stage_counts.get("report", 0) + 1
            except Exception as exc:
                audit.issue(
                    stage="runner",
                    sim_now=item.sim_ts,
                    reading_index=item.reading_index,
                    message=str(exc),
                    traceback=traceback.format_exc(),
                )
                if fail_fast:
                    break

        end_now = clock.now()
        # The wrap-up stages must never abort the run silently: a failure here
        # previously escaped run() entirely, so no simulation_report.json/.md was
        # written and the CLI died with a raw traceback after replaying the full
        # dataset. Record the failure and still emit the audit artifacts.
        if inserted_this_run > 0:
            try:
                memory_correlation = f"memory:wrapup:{end_now.isoformat()}"
                counts_before = _pipeline_counts(
                    memory_repository, SQLiteReportRepository(store), self.user_id
                )
                self._daily_memory(
                    cgm_repository,
                    memory_repository,
                    consolidation,
                    analytics,
                    detector,
                    end_now,
                )
                counts_after = _pipeline_counts(
                    memory_repository, SQLiteReportRepository(store), self.user_id
                )
                audit.record(
                    "memory",
                    correlation_id=memory_correlation,
                    sim_now=end_now.isoformat(),
                    window_start=(end_now - timedelta(days=1)).isoformat(),
                    window_end=end_now.isoformat(),
                    counts_before=counts_before,
                    counts_after=counts_after,
                )
                stage_counts["memory"] = stage_counts.get("memory", 0) + 1
                self._build_l0(
                    cgm_repository, analytics, detector,
                    end_now, audit, f"wrapup:{end_now.isoformat()}",
                    stage_counts,
                )
                generated_report = reporter.generate(
                    ReportInput(
                        report_type="weekly",
                        user_id=self.user_id,
                        audience="self",
                        anchor_at=end_now,
                        timezone=self.timezone_name,
                    )
                )
                audit.record(
                    "report",
                    correlation_id=f"report:{generated_report.report_id}",
                    parent_correlation_id=memory_correlation,
                    sim_now=end_now.isoformat(),
                    report_id=generated_report.report_id,
                    report_type="weekly",
                )
                audit.link(
                    from_stage="memory",
                    from_id=memory_correlation,
                    to_stage="report",
                    to_id=generated_report.report_id,
                    relation="informed",
                )
                stage_counts["report"] = stage_counts.get("report", 0) + 1
            except Exception as exc:
                audit.issue(
                    stage="wrapup",
                    sim_now=end_now,
                    reading_index=None,
                    message=str(exc),
                    traceback=traceback.format_exc(),
                )

        totals = ingest.totals()
        db_count = len(cgm_repository.list_glucose_points(replay_scope))
        db_delta = db_count - preexisting_count
        # Analytics determinism (Constitution Principle I): recomputing the same
        # window must yield byte-identical metrics. A mismatch means the
        # deterministic-metrics guarantee is broken — exactly the class of bug
        # this harness exists to catch.
        det_scope = DataScope(
            user_id=self.user_id,
            window_start=records[-1].sim_ts - timedelta(hours=24),
            window_end=records[-1].sim_ts + timedelta(minutes=5),
            source=self.source_label,
        )
        try:
            det_points = cgm_repository.list_glucose_points(det_scope)
            agg_first = analytics.compute_aggregate(points=det_points, scope=det_scope)
            agg_second = analytics.compute_aggregate(points=det_points, scope=det_scope)
            deterministic = agg_first.model_dump() == agg_second.model_dump()
            if not deterministic:
                audit.issue(
                    stage="invariant",
                    sim_now=end_now,
                    reading_index=None,
                    message="analytics.compute_aggregate produced different results for the same window",
                )
        except Exception as exc:
            deterministic = False
            audit.issue(
                stage="invariant",
                sim_now=end_now,
                reading_index=None,
                message=f"analytics determinism check raised: {exc}",
                traceback=traceback.format_exc(),
            )
        audit.set_invariant("analytics_deterministic", deterministic)
        emitted_equals_accounted = (
            len(records) == totals.inserted + totals.duplicate + totals.issues
        )
        db_delta_matches_inserted = db_delta == totals.inserted
        audit.set_invariant("emitted_equals_accounted", emitted_equals_accounted)
        # Backward-compatible key; its semantics are now correct for both a
        # clean run and a restart against a partially/fully populated DB.
        audit.set_invariant("db_count_matches_inserted", db_delta_matches_inserted)
        audit.set_invariant("db_delta_matches_inserted", db_delta_matches_inserted)
        audit.set_invariant("preexisting_db_count", preexisting_count)
        audit.set_invariant("db_delta", db_delta)
        audit.set_invariant("db_count", db_count)
        audit.set_invariant("emitted", len(records))
        audit.set_invariant("inserted", totals.inserted)
        audit.set_invariant("duplicate", totals.duplicate)
        audit.set_invariant("issues", totals.issues)
        audit.set_invariant("stage_counts", dict(sorted(stage_counts.items())))

        memory_counts = _pipeline_counts(
            memory_repository,
            SQLiteReportRepository(store),
            self.user_id,
        )
        audit.set_invariant("pipeline_counts_before", pipeline_counts_before)
        audit.set_invariant("pipeline_counts", memory_counts)

        audit.require(
            "all_emitted_records_accounted",
            emitted_equals_accounted,
            message="emitted count does not equal inserted + duplicate + import issues",
            expected=len(records),
            actual=totals.inserted + totals.duplicate + totals.issues,
        )
        audit.require(
            "database_delta_matches_inserted",
            db_delta_matches_inserted,
            message="database row-count delta does not match inserted count",
            expected=totals.inserted,
            actual=db_delta,
        )
        audit.require(
            "ingest_stage_complete",
            stage_counts.get("ingest", 0) == len(records),
            message="ingest audit stage count does not match emitted records",
            expected=len(records),
            actual=stage_counts.get("ingest", 0),
        )
        audit.require(
            "analytics_deterministic",
            deterministic,
            message="analytics are not deterministic for an identical window",
            expected=True,
            actual=deterministic,
        )

        duration = records[-1].sim_ts - records[0].sim_ts
        if totals.inserted == 0:
            audit.require(
                "duplicate_replay_downstream_idempotent",
                memory_counts == pipeline_counts_before,
                message="duplicate-only replay changed downstream memory/report counts",
                expected=pipeline_counts_before,
                actual=memory_counts,
            )
        elif duration >= timedelta(hours=24):
            audit.require(
                "long_run_hourly_stage_present",
                stage_counts.get("hourly", 0) > 0,
                message="24h+ replay produced no hourly analytics stage",
            )
            audit.require(
                "long_run_memory_stage_present",
                stage_counts.get("memory", 0) > 0 and memory_counts["warm_summaries"] > 0,
                message="24h+ replay produced no durable memory summary",
            )
            audit.require(
                "long_run_report_stage_present",
                stage_counts.get("report", 0) > 0 and memory_counts["reports"] > 0,
                message="24h+ replay produced no durable report",
            )
            audit.require(
                "long_run_push_stage_present",
                stage_counts.get("push", 0) > 0,
                message="24h+ replay exercised no push scheduling stage",
            )
        # L0 acceptance: 24h+ runs must build L0 context at least once
        if duration >= timedelta(hours=24) and totals.inserted > 0:
            audit.require(
                "l0_context_built",
                stage_counts.get("l0", 0) > 0,
                message="24h+ replay did not build L0 context",
                expected=True,
                actual=stage_counts.get("l0", 0) > 0,
            )
        # L2/L3 acceptance: 72h+ runs with L1 episodes must generate beliefs
        # and hypotheses (l2_min_episodes=3, l3_min_pattern=3 distinct days)
        if (
            duration >= timedelta(hours=72)
            and totals.inserted > 0
            and memory_counts["l1"] > 0
        ):
            audit.require(
                "l2_belief_generated",
                memory_counts["l2"] > 0,
                message="72h+ replay with L1 episodes did not generate L2 beliefs",
                expected=True,
                actual=memory_counts["l2"] > 0,
            )
            audit.require(
                "l3_hypothesis_generated",
                memory_counts["l3"] > 0,
                message="72h+ replay with L1 episodes did not generate L3 hypotheses",
                expected=True,
                actual=memory_counts["l3"] > 0,
            )
        json_path, md_path = audit.write()
        status = "ok" if not audit.issues else "failed"
        return SimulationRunResult(
            status=status,
            exit_code=0 if status == "ok" else 1,
            run_id=run_id,
            out_dir=self.out_dir,
            db_path=self.db_path,
            emitted=len(records),
            inserted=totals.inserted,
            duplicate=totals.duplicate,
            issues=len(audit.issues),
            report_json=json_path,
            report_md=md_path,
            stage_counts=stage_counts,
        )

    def _hourly(
        self,
        repository: SQLiteCGMRepository,
        analytics: CGMAnalyticsService,
        detector: GlucoseEventDetector,
        audit: SimulationAudit,
        boundary: datetime,
    ) -> None:
        scope = DataScope(
            user_id=self.user_id,
            window_start=boundary - timedelta(hours=24),
            window_end=boundary,
            source=self.source_label,
        )
        points = repository.list_glucose_points(scope)
        aggregate = analytics.compute_aggregate(points=points, scope=scope)
        events = detector.detect(points=points, scope=scope)
        inserted_events = 0
        inserted_event_ids: list[str] = []
        for event in events:
            try:
                repository.create_glucose_event(event)
                inserted_events += 1
                inserted_event_ids.append(event.event_id)
            except sqlite3.IntegrityError:
                pass
        audit.record(
            "hourly",
            correlation_id=f"hourly:{boundary.isoformat()}",
            sim_now=boundary.isoformat(),
            window_start=scope.window_start.isoformat(),
            window_end=scope.window_end.isoformat(),
            point_count=aggregate.point_count,
            detected_events=len(events),
            inserted_events=inserted_events,
            inserted_event_ids=inserted_event_ids,
        )
        for event_id in inserted_event_ids:
            audit.link(
                from_stage="hourly",
                from_id=f"hourly:{boundary.isoformat()}",
                to_stage="event",
                to_id=event_id,
                relation="detected",
            )

    def _daily_memory(
        self,
        cgm_repository: SQLiteCGMRepository,
        memory_repository: SQLiteMemoryRepository,
        consolidation: ConsolidationService,
        analytics: CGMAnalyticsService,
        detector: GlucoseEventDetector,
        now: datetime,
    ) -> None:
        scope = DataScope(
            user_id=self.user_id,
            window_start=now - timedelta(days=1),
            window_end=now,
            source=self.source_label,
        )
        points = cgm_repository.list_glucose_points(scope)
        events = detector.detect(points=points, scope=scope)
        for episode in episodes_from_detected_events(events, now=now, timezone_name=self.timezone_name):
            try:
                memory_repository.create_episode(episode)
            except sqlite3.IntegrityError:
                pass
        aggregate = analytics.compute_aggregate(points=points, scope=scope, window_label="day")
        consolidation.consolidate(self.user_id, now=now)
        consolidation.synthesize_state(
            user_id=self.user_id,
            window_start=scope.window_start,
            window_end=scope.window_end,
            period="daily",
            metrics_summary={"tir_pct": aggregate.tir, "mean_mgdl": aggregate.mbg},
            now=now,
        )

    def _build_l0(
        self,
        cgm_repository: SQLiteCGMRepository,
        analytics: CGMAnalyticsService,
        detector: GlucoseEventDetector,
        now: datetime,
        audit: SimulationAudit,
        day_key: str,
        stage_counts: dict[str, int],
    ) -> bool:
        """Build L0 real-time context (D038), record to audit and stage_counts."""
        try:
            builder = L0ContextBuilder(
                repository=cgm_repository,
                analytics_service=analytics,
                event_detector=detector,
                config=L0BuildConfig(timezone=self.timezone_name),
            )
            context = builder.build(
                user_id=self.user_id,
                anchor_at=now,
                source=self.source_label,
            )
            ok = context.window_summary.point_count > 0
            if ok:
                stage_counts["l0"] = stage_counts.get("l0", 0) + 1
                audit.record(
                    "l0",
                    correlation_id=f"l0:{day_key}",
                    sim_now=now.isoformat(),
                    point_count=context.window_summary.point_count,
                    daily_aggregates=len(context.daily_aggregates),
                    key_events=len(context.key_glucose_events),
                    data_quality_warnings=len(context.data_quality),
                )
            return ok
        except Exception as exc:
            audit.issue(stage="l0", sim_now=now, message=str(exc))
            return False

    def _result(
        self,
        status: str,
        exit_code: int,
        run_id: str,
        audit: SimulationAudit,
        emitted: int,
        json_path: Path,
        md_path: Path,
    ) -> SimulationRunResult:
        totals = {"inserted": 0, "duplicate": 0, "issues": len(audit.issues)}
        return SimulationRunResult(
            status=status,
            exit_code=exit_code,
            run_id=run_id,
            out_dir=self.out_dir,
            db_path=self.db_path,
            emitted=emitted,
            inserted=totals["inserted"],
            duplicate=totals["duplicate"],
            issues=totals["issues"],
            report_json=json_path,
            report_md=md_path,
        )


def _infer_interval_minutes(records: list) -> int:
    """Device cadence of the replayed CSV via the shared median helper (D053)."""
    from hermes_cgm_agent.services.analytics import median_interval_minutes

    return median_interval_minutes([record.sim_ts for record in records])


def _ceil_hour(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    floored = value.replace(minute=0, second=0)
    if floored == value:
        return value
    return floored + timedelta(hours=1)


def _pipeline_counts(
    memory_repository: SQLiteMemoryRepository,
    report_repository: SQLiteReportRepository,
    user_id: str,
) -> dict[str, int]:
    """Durable stage evidence used by restart/idempotency acceptance checks."""
    return {
        "l1": len(memory_repository.list_episodes(user_id, include_archived=True)),
        "l2": len(memory_repository.list_profile_items(user_id, active_only=False)),
        "l3": len(memory_repository.list_hypotheses(user_id, active_only=False)),
        "warm_summaries": len(memory_repository.list_summaries(user_id)),
        "reports": len(report_repository.list_reports(user_id=user_id, limit=10000)),
    }
