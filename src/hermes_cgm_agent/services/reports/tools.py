from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cgm_agent.domain import Report
from hermes_cgm_agent.domain.report import ReportInput
from hermes_cgm_agent.services.analytics import (
    AnalyticsConfig,
    CGMAnalyticsService,
    EventDetectionConfig,
    GlucoseEventDetector,
    median_interval_minutes,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import (
    MemoryContextAssembler,
    MemoryToolService,
    SQLiteMemoryRepository,
)
from hermes_cgm_agent.services.reports.builder import ReportService
from hermes_cgm_agent.services.reports.builder import resolve_report_scope
from hermes_cgm_agent.services.reports.repository import SQLiteReportRepository
from hermes_cgm_agent.services.reports.retrieval_query import build_authoritative_query
from hermes_cgm_agent.services.reports.sections.helpers import _window_label  # L-11: deduplicate
from hermes_cgm_agent.services.safety import assert_track_isolation
from hermes_cgm_agent.services.arguments import optional_bool, require_bool


@dataclass(frozen=True)
class ReportToolResult:
    report: Report
    memory_ingest: dict[str, Any]


class ReportToolService:
    """Business orchestration behind reports.generate, without audit wiring."""

    def __init__(
        self,
        *,
        cgm_repository: SQLiteCGMRepository,
        report_repository: SQLiteReportRepository,
        memory_repository: SQLiteMemoryRepository,
    ) -> None:
        self.cgm_repository = cgm_repository
        self.report_repository = report_repository
        self.memory_repository = memory_repository

    def generate(self, arguments: dict[str, Any]) -> ReportToolResult:
        args = dict(arguments)
        retrieve_context = optional_bool(
            args.pop("retrieve_context", None),
            "retrieve_context",
            default=True,
        )
        auto_ingest_memory = auto_ingest_memory_enabled(args)
        args.pop("auto_ingest_memory", None)
        if retrieve_context:
            args = self._inject_retrieved_context(args)
        report_input = ReportInput.model_validate(args)
        report = ReportService(
            cgm_repository=self.cgm_repository,
            report_repository=self.report_repository,
        ).generate(report_input)
        memory_ingest = MemoryToolService(self.memory_repository).ingest_report_candidates(
            report=report,
            enabled=auto_ingest_memory,
        )
        return ReportToolResult(report=report, memory_ingest=memory_ingest)

    def _inject_retrieved_context(self, args: dict[str, Any]) -> dict[str, Any]:
        """Populate report RAG slots without crossing fact and KB tracks."""
        user_id = args.get("user_id") or (args.get("data_scope") or {}).get("user_id")
        if not user_id:
            return args
        assembler = MemoryContextAssembler(repository=self.memory_repository)
        report_type = str(args.get("report_type", "daily"))
        memory_query = f"{report_type} review for {user_id}"
        if "memory_context" not in args:
            args["memory_context"] = assembler.build_memory_context(
                user_id=str(user_id),
                query=memory_query,
            ).model_dump(mode="json")
        if "authoritative_context" not in args:
            authoritative_query, population = self._authoritative_query(args)
            args["authoritative_context"] = assembler.build_authoritative_context(
                query=authoritative_query,
                top_k=3,
                population=population,
            ).model_dump(mode="json")
        # D031: fail loudly if the two memory tracks ever cross-contaminate.
        assert_track_isolation(
            memory_items=(args.get("memory_context") or {}).get("items", []),
            authoritative_documents=(args.get("authoritative_context") or {}).get(
                "documents",
                [],
            ),
        )
        return args

    def _authoritative_query(self, args: dict[str, Any]) -> tuple[str, str | None]:
        report_input = ReportInput.model_validate(args)
        report_type = str(report_input.report_type)
        scope = report_input.data_scope or resolve_report_scope(
            user_id=report_input.user_id or "",
            report_type=report_input.report_type,
            timezone_name=report_input.timezone,
            anchor_time=report_input.report_anchor_time,
            anchor_at=report_input.anchor_at,
        )
        points = self.cgm_repository.list_glucose_points(scope)
        interval = median_interval_minutes([point.timestamp for point in points]) if points else 5
        analytics = CGMAnalyticsService(
            AnalyticsConfig(expected_interval_minutes=int(round(interval)) or 5)
        )
        detector = GlucoseEventDetector(
            EventDetectionConfig(
                expected_interval_minutes=int(round(interval)) or 5,
                timezone=report_input.timezone,
            )
        )
        aggregate = analytics.compute_aggregate(
            points=points,
            scope=scope,
            window_label=_window_label(report_type),
        )
        detected_events = detector.detect(points=points, scope=scope)
        population = str(args.get("population") or "").strip() or None
        return build_authoritative_query(
            aggregate=aggregate,
            detected_events=detected_events,
            population=population,
            report_type=report_type,
        )


def auto_ingest_memory_enabled(arguments: dict[str, Any]) -> bool:
    value = arguments.get("auto_ingest_memory")
    if value is not None:
        return require_bool(value, "auto_ingest_memory")
    # Doctor-facing reports should not silently queue personal memory because
    # their audience and wording are optimized for clinicians, not self-review.
    return str(arguments.get("report_type", "")).lower() != "doctor"
