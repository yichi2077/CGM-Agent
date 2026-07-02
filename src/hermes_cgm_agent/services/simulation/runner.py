from __future__ import annotations

import sqlite3
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.domain.report import ReportInput
from hermes_cgm_agent.services.analytics import CGMAnalyticsService, GlucoseEventDetector
from hermes_cgm_agent.services.memory import ConsolidationService, SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.derive import episodes_from_detected_events
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
        timezone_name: str = "Asia/Shanghai",
        acceleration: float = 300.0,
        max_speed: bool = False,
    ) -> None:
        self.db_path = db_path
        self.out_dir = out_dir
        self.user_id = user_id
        self.source_label = source_label
        self.timezone_name = timezone_name
        self.acceleration = acceleration
        self.max_speed = max_speed

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

        clock = SimClock(
            start=records[0].sim_ts,
            acceleration=self.acceleration,
            max_speed=self.max_speed,
        )
        analytics = CGMAnalyticsService()
        detector = GlucoseEventDetector()
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

        for item in records:
            try:
                sim_now = clock.advance_to(item.sim_ts)
                ingest.ingest_record(item.record, batch_id=source.batch.batch_id)
                audit.record(
                    "ingest",
                    sim_now=sim_now.isoformat(),
                    reading_index=item.reading_index,
                )
                stage_counts["ingest"] = stage_counts.get("ingest", 0) + 1

                while sim_now >= next_hour:
                    self._hourly(cgm_repository, analytics, detector, audit, next_hour)
                    stage_counts["hourly"] = stage_counts.get("hourly", 0) + 1
                    next_hour += timedelta(hours=1)

                local_now = sim_now.astimezone(ZoneInfo(self.timezone_name))
                day_key = local_now.strftime("%Y-%m-%d")
                if local_now.hour >= 9 and day_key not in seen_daily_push:
                    first_push = scheduler.push_tick(user_id=self.user_id, now=sim_now)
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
                    self._daily_memory(
                        cgm_repository,
                        memory_repository,
                        consolidation,
                        analytics,
                        detector,
                        sim_now,
                    )
                    seen_daily_memory.add(day_key)
                    stage_counts["memory"] = stage_counts.get("memory", 0) + 1

                    reporter.generate(
                        ReportInput(
                            report_type="daily",
                            user_id=self.user_id,
                            audience="self",
                            anchor_at=sim_now,
                            timezone=self.timezone_name,
                        )
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
        self._daily_memory(
            cgm_repository,
            memory_repository,
            consolidation,
            analytics,
            detector,
            end_now,
        )
        reporter.generate(
            ReportInput(
                report_type="weekly",
                user_id=self.user_id,
                audience="self",
                anchor_at=end_now,
                timezone=self.timezone_name,
            )
        )
        stage_counts["report"] = stage_counts.get("report", 0) + 1

        totals = ingest.totals()
        db_count = len(
            cgm_repository.list_glucose_points(
                DataScope(
                    user_id=self.user_id,
                    window_start=records[0].sim_ts,
                    window_end=records[-1].sim_ts + timedelta(minutes=5),
                    source=self.source_label,
                )
            )
        )
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
        det_points = cgm_repository.list_glucose_points(det_scope)
        agg_first = analytics.compute_aggregate(points=det_points, scope=det_scope)
        agg_second = analytics.compute_aggregate(points=det_points, scope=det_scope)
        deterministic = agg_first.model_dump() == agg_second.model_dump()
        audit.set_invariant("analytics_deterministic", deterministic)
        if not deterministic:
            audit.issue(
                stage="invariant",
                sim_now=end_now,
                reading_index=None,
                message="analytics.compute_aggregate produced different results for the same window",
            )
        audit.set_invariant("emitted_equals_accounted", len(records) == totals.inserted + totals.duplicate + totals.issues)
        audit.set_invariant("db_count_matches_inserted", db_count == totals.inserted)
        audit.set_invariant("db_count", db_count)
        audit.set_invariant("emitted", len(records))
        audit.set_invariant("inserted", totals.inserted)
        audit.set_invariant("duplicate", totals.duplicate)
        audit.set_invariant("issues", totals.issues)
        if not audit.invariants["emitted_equals_accounted"]:
            audit.issue(stage="invariant", sim_now=end_now, reading_index=None, message="emitted count does not equal inserted + duplicate + issues")
        if not audit.invariants["db_count_matches_inserted"]:
            audit.issue(stage="invariant", sim_now=end_now, reading_index=None, message="DB count does not match inserted count")
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
            issues=totals.issues,
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
        for event in events:
            try:
                repository.create_glucose_event(event)
                inserted_events += 1
            except sqlite3.IntegrityError:
                pass
        audit.record(
            "hourly",
            sim_now=boundary.isoformat(),
            point_count=aggregate.point_count,
            detected_events=len(events),
            inserted_events=inserted_events,
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
        for episode in episodes_from_detected_events(events, now=now):
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


def _ceil_hour(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    floored = value.replace(minute=0, second=0)
    if floored == value:
        return value
    return floored + timedelta(hours=1)
