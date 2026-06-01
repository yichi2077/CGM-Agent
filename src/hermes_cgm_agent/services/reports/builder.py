from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from collections import Counter

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    UserEvent,
)
from hermes_cgm_agent.domain.report import (
    AuthoritativeContext,
    DataQualityWarning,
    G8MemoryCandidate,
    MemoryContext,
    Report,
    ReportAudience,
    ReportInput,
    ReportSection,
    ReportSourceTrack,
    ReportType,
)
from hermes_cgm_agent.services.analytics import (
    CGMAnalyticsService,
    GlucoseEventDetector,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.reports.renderer import render_markdown
from hermes_cgm_agent.services.reports.repository import SQLiteReportRepository


REPORT_WINDOW_DAYS = {
    ReportType.DAILY: 1,
    ReportType.WEEKLY: 7,
    ReportType.DOCTOR: 14,
}


class ReportService:
    def __init__(
        self,
        *,
        cgm_repository: SQLiteCGMRepository,
        report_repository: SQLiteReportRepository,
        analytics_service: CGMAnalyticsService | None = None,
        event_detector: GlucoseEventDetector | None = None,
    ) -> None:
        self.cgm_repository = cgm_repository
        self.report_repository = report_repository
        self.analytics_service = analytics_service or CGMAnalyticsService()
        self.event_detector = event_detector or GlucoseEventDetector()

    def generate(self, report_input: ReportInput) -> Report:
        report_type = ReportType(report_input.report_type)
        scope = report_input.data_scope or resolve_report_scope(
            user_id=report_input.user_id or "",
            report_type=report_type,
            timezone_name=report_input.timezone,
            anchor_time=report_input.report_anchor_time,
            anchor_at=report_input.anchor_at,
        )
        report_id = uuid.uuid4().hex
        points = self.cgm_repository.list_glucose_points(scope)
        aggregate = self.analytics_service.compute_aggregate(
            points=points,
            scope=scope,
            window_label=_window_label(report_type),
        )
        events = self.cgm_repository.list_user_events(scope, include_rejected=False)
        if not report_input.include_candidate_events:
            events = [event for event in events if event.user_confirmed]
        detected_events = self.event_detector.detect(points=points, scope=scope)
        warnings = self._data_quality_warnings(points=points, aggregate=aggregate)
        sections = self._sections(
            report_id=report_id,
            report_input=report_input,
            scope=scope,
            aggregate=aggregate,
            events=events,
            detected_events=detected_events,
            warnings=warnings,
        )
        candidates = [
            candidate
            for section in sections
            for candidate in section.g8_memory_candidates
        ]
        evidence_refs = _unique_evidence_refs(
            ref for section in sections for ref in section.evidence_refs
        )
        report = Report(
            report_id=report_id,
            user_id=scope.user_id,
            report_type=report_type,
            audience=report_input.audience,
            data_scope=scope,
            timezone=report_input.timezone,
            report_anchor_time=report_input.report_anchor_time,
            sections=sections,
            evidence_refs=evidence_refs,
            data_quality_warnings=warnings,
            g8_memory_candidates=candidates,
            source_versions={
                "report_contract": "G7",
                "analytics": "g7-analytics-v2",
                "event_detector": "g6-detector-v1",
                "memory_context": _context_version(report_input.memory_context),
                "authoritative_context": _context_version(report_input.authoritative_context),
            },
        )
        report.rendered_markdown = render_markdown(report)
        report.output_hash = _output_hash(report.rendered_markdown)
        return self.report_repository.create_report(report)

    def _sections(
        self,
        *,
        report_id: str,
        report_input: ReportInput,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
        warnings: list[DataQualityWarning],
    ) -> list[ReportSection]:
        sections = [
            self._overview_section(scope, aggregate, warnings),
            self._metrics_section(scope, aggregate),
            self._data_quality_section(scope, warnings),
            self._key_events_section(report_id, scope, events),
            self._detected_events_section(scope, detected_events),
            self._observations_section(
                scope,
                aggregate,
                report_input.memory_context,
                report_input.authoritative_context,
            ),
            self._follow_up_section(scope, aggregate, events),
        ]
        if ReportType(report_input.report_type) == ReportType.WEEKLY:
            sections.append(
                self._patterns_section(report_id, scope, aggregate, events, detected_events)
            )
        if ReportType(report_input.report_type) == ReportType.DOCTOR:
            sections.append(
                self._doctor_appendix_section(scope, aggregate, events, detected_events, warnings)
            )
        return sections

    def _overview_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        warnings: list[DataQualityWarning],
    ) -> ReportSection:
        content = (
            f"This report covers {scope.window_start.isoformat()} to {scope.window_end.isoformat()}. "
            f"It includes {aggregate.point_count} valid CGM points with {aggregate.data_coverage}% coverage."
        )
        if warnings:
            content += " Data quality warnings are present and should limit interpretation."
        return ReportSection(
            section_id="overview",
            kind="overview",
            title="Overview",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=warnings,
        )

    def _metrics_section(self, scope: DataScope, aggregate: GlucoseAggregate) -> ReportSection:
        content = (
            f"TIR: {aggregate.tir}%; TAR: {aggregate.tar}%; TBR: {aggregate.tbr}%; "
            f"MBG: {aggregate.mbg} mg/dL; CV: {aggregate.cv}%; GMI: {aggregate.gmi}; "
            f"Data coverage: {aggregate.data_coverage}%."
        )
        return ReportSection(
            section_id="metrics",
            kind="metrics",
            title="Metrics",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
        )

    def _data_quality_section(
        self,
        scope: DataScope,
        warnings: list[DataQualityWarning],
    ) -> ReportSection:
        if not warnings:
            content = "No data quality warnings were detected for this report window."
        else:
            content = "Data quality warnings: " + "; ".join(warning.message for warning in warnings)
        return ReportSection(
            section_id="data_quality",
            kind="data_quality",
            title="Data Quality",
            content=content,
            data_scope=scope,
            evidence_refs=[ref for warning in warnings for ref in warning.evidence_refs],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=1.0,
            warnings=warnings,
        )

    def _key_events_section(
        self,
        report_id: str,
        scope: DataScope,
        events: list[UserEvent],
    ) -> ReportSection:
        confirmed = [event for event in events if event.user_confirmed]
        candidates = [event for event in events if not event.user_confirmed]
        evidence_refs = [_event_evidence(event) for event in events]
        memory_candidates = [
            G8MemoryCandidate(
                target_layer="L1",
                candidate_type="episode",
                summary=f"Confirmed {event.event_type} event at {event.ts_start.isoformat()} in report window.",
                source_report_id=report_id,
                source_section_id="key_events",
                evidence_refs=[_event_evidence(event)],
                confidence=event.confidence if event.confidence is not None else 0.7,
                requires_user_confirmation=False,
            )
            for event in confirmed
        ]
        if not events:
            content = "No timeline events were found in this report window."
        else:
            content = (
                f"Confirmed events: {len(confirmed)}. "
                f"Unconfirmed candidate events: {len(candidates)}."
            )
        return ReportSection(
            section_id="key_events",
            kind="key_events",
            title="Key Events",
            content=content,
            data_scope=scope,
            evidence_refs=evidence_refs,
            source_tracks=[ReportSourceTrack.FACT],
            confidence=1.0 if not candidates else 0.8,
            g8_memory_candidates=memory_candidates,
        )

    def _detected_events_section(
        self,
        scope: DataScope,
        detected_events: list[GlucoseEvent],
    ) -> ReportSection:
        if not detected_events:
            content = "No glucose events were detected in this report window."
        else:
            counts = Counter(str(event.event_type) for event in detected_events)
            alerts = [event for event in detected_events if str(event.severity) == "alert"]
            parts = ", ".join(f"{label}: {count}" for label, count in sorted(counts.items()))
            content = f"Detected glucose events ({len(detected_events)}): {parts}."
            if alerts:
                content += f" {len(alerts)} reached alert severity."
        return ReportSection(
            section_id="detected_events",
            kind="detected_events",
            title="Detected Glucose Events",
            content=content,
            data_scope=scope,
            evidence_refs=[
                ref for event in detected_events for ref in event.evidence_refs
            ],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=1.0,
        )

    def _observations_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        memory_context: MemoryContext,
        authoritative_context: AuthoritativeContext,
    ) -> ReportSection:
        observations = []
        if aggregate.point_count == 0:
            observations.append("There are no valid CGM points in this report window.")
        elif (aggregate.tar or 0) > (aggregate.tbr or 0) and (aggregate.tar or 0) > 0:
            observations.append("The window shows more above-range time than below-range time.")
        elif (aggregate.tbr or 0) > 0:
            observations.append("The window includes below-range time.")
        else:
            observations.append("The available valid points are mostly within the configured range.")

        source_tracks = [ReportSourceTrack.FACT]
        evidence_refs = [_aggregate_evidence(scope, aggregate.window_label)]
        memory_refs = _context_evidence_refs(memory_context.items)
        authoritative_refs = _context_evidence_refs(authoritative_context.documents)
        if memory_refs:
            source_tracks.append(ReportSourceTrack.USER_MEMORY)
            evidence_refs.extend(memory_refs)
            observations.append("Memory context was supplied for later G8-aware interpretation.")
        if authoritative_refs:
            source_tracks.append(ReportSourceTrack.AUTHORITATIVE)
            evidence_refs.extend(authoritative_refs)
            observations.append("Authoritative context was supplied for later G8-aware interpretation.")
        if len(source_tracks) > 1:
            source_tracks.append(ReportSourceTrack.MIXED)

        return ReportSection(
            section_id="observations",
            kind="observations",
            title="Observations",
            content=" ".join(observations),
            data_scope=scope,
            evidence_refs=evidence_refs,
            source_tracks=_unique_source_tracks(source_tracks),
            confidence=_coverage_confidence(aggregate.data_coverage),
        )

    def _follow_up_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
    ) -> ReportSection:
        prompts = []
        if any(not event.user_confirmed for event in events):
            prompts.append("Review and confirm or reject candidate events in this window.")
        if aggregate.point_count == 0 or aggregate.data_coverage < 70:
            prompts.append("Check whether sensor gaps, warmup, or missing data should be recorded.")
        if not events:
            prompts.append("Add meal, exercise, sleep, or note events if they explain this window.")
        return ReportSection(
            section_id="follow_up_prompts",
            kind="follow_up_prompts",
            title="Follow-Up Prompts",
            content=" ".join(prompts) if prompts else "No follow-up prompts were generated.",
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)] + [_event_evidence(event) for event in events],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=0.8,
        )

    def _patterns_section(
        self,
        report_id: str,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
    ) -> ReportSection:
        evidence_refs = [_aggregate_evidence(scope, aggregate.window_label)] + [
            _event_evidence(event) for event in events if event.user_confirmed
        ]
        # Repetition analysis over detected glucose events: a pattern needs the
        # same event type recurring on multiple distinct local days, not just a
        # single window-level aggregate threshold (audit P1-3 fix).
        repeated = self._repeated_event_patterns(detected_events)
        candidate_summaries: list[str] = []
        for event_type, day_count in repeated:
            label = event_type.replace("_", " ")
            candidate_summaries.append(
                f"Repeated {label} events on {day_count} separate days this week."
            )
            evidence_refs.extend(
                ref
                for event in detected_events
                if str(event.event_type) == event_type
                for ref in event.evidence_refs
            )
        if not candidate_summaries:
            if (aggregate.tar or 0) >= 20:
                candidate_summaries.append("Above-range time is elevated in this weekly window.")
            elif (aggregate.tbr or 0) >= 5:
                candidate_summaries.append("Below-range time appears in this weekly window.")
            else:
                candidate_summaries.append("Weekly pattern candidate requires more evidence.")

        candidates = [
            G8MemoryCandidate(
                target_layer="L3",
                candidate_type="hypothesis",
                summary=summary,
                source_report_id=report_id,
                source_section_id="patterns",
                evidence_refs=_unique_evidence_refs(evidence_refs),
                confidence=_coverage_confidence(aggregate.data_coverage),
                requires_user_confirmation=True,
            )
            for summary in candidate_summaries
        ]
        return ReportSection(
            section_id="patterns",
            kind="patterns",
            title="Patterns",
            content="Candidate patterns: " + " ".join(candidate_summaries),
            data_scope=scope,
            evidence_refs=_unique_evidence_refs(evidence_refs),
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            g8_memory_candidates=candidates,
        )

    def _repeated_event_patterns(
        self,
        detected_events: list[GlucoseEvent],
        *,
        min_days: int = 2,
        timezone_name: str = "Asia/Shanghai",
    ) -> list[tuple[str, int]]:
        local_zone = ZoneInfo(timezone_name)
        days_by_type: dict[str, set] = {}
        for event in detected_events:
            local_day = event.ts_start.astimezone(local_zone).date()
            days_by_type.setdefault(str(event.event_type), set()).add(local_day)
        repeated = [
            (event_type, len(days))
            for event_type, days in days_by_type.items()
            if len(days) >= min_days
        ]
        return sorted(repeated, key=lambda item: (-item[1], item[0]))

    def _doctor_appendix_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
        warnings: list[DataQualityWarning],
    ) -> ReportSection:
        content = (
            "Structured appendix: "
            f"TIR={aggregate.tir}%, TAR={aggregate.tar}%, TBR={aggregate.tbr}%, "
            f"MBG={aggregate.mbg} mg/dL, CV={aggregate.cv}%, GMI={aggregate.gmi}, "
            f"LBGI={aggregate.lbgi}, HBGI={aggregate.hbgi}, "
            f"coverage={aggregate.data_coverage}%, confirmed_events={len([event for event in events if event.user_confirmed])}, "
            f"detected_glucose_events={len(detected_events)}, "
            f"warnings={len(warnings)}."
        )
        return ReportSection(
            section_id="doctor_appendix",
            kind="doctor_appendix",
            title="Doctor Appendix",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)] + [_event_evidence(event) for event in events],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=warnings,
        )

    def _data_quality_warnings(
        self,
        *,
        points: list[GlucosePoint],
        aggregate: GlucoseAggregate,
    ) -> list[DataQualityWarning]:
        warnings: list[DataQualityWarning] = []
        aggregate_ref = _aggregate_evidence(
            DataScope(
                user_id=aggregate.user_id,
                window_start=aggregate.window_start,
                window_end=aggregate.window_end,
            ),
            aggregate.window_label,
        )
        if aggregate.point_count == 0:
            warnings.append(
                DataQualityWarning(
                    code="no_valid_points",
                    message="No valid CGM points were available in the report window.",
                    severity="warning",
                    evidence_refs=[aggregate_ref],
                )
            )
        elif aggregate.data_coverage < 70:
            warnings.append(
                DataQualityWarning(
                    code="low_coverage",
                    message=f"Data coverage is {aggregate.data_coverage}%, below the 70% review threshold.",
                    severity="warning",
                    evidence_refs=[aggregate_ref],
                )
            )
        non_valid_count = len([point for point in points if str(point.quality_flag) != "valid"])
        if non_valid_count:
            warnings.append(
                DataQualityWarning(
                    code="non_valid_points_present",
                    message=f"{non_valid_count} non-valid CGM points were excluded from metric calculation.",
                    severity="info",
                    evidence_refs=[aggregate_ref],
                )
            )
        return warnings


def resolve_report_scope(
    *,
    user_id: str,
    report_type: ReportType | str,
    timezone_name: str = "Asia/Shanghai",
    anchor_time: time = time(7, 0),
    anchor_at: datetime | None = None,
) -> DataScope:
    parsed_type = ReportType(report_type)
    local_zone = ZoneInfo(timezone_name)
    now = anchor_at or datetime.now(timezone.utc)
    local_now = now.astimezone(local_zone)
    local_anchor = local_now.replace(
        hour=anchor_time.hour,
        minute=anchor_time.minute,
        second=anchor_time.second,
        microsecond=0,
    )
    if local_now < local_anchor:
        local_anchor = local_anchor - timedelta(days=1)
    window_end = local_anchor.astimezone(timezone.utc)
    window_start = window_end - timedelta(days=REPORT_WINDOW_DAYS[parsed_type])
    return DataScope(
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
    )


def _window_label(report_type: ReportType | str) -> str:
    report_type = ReportType(report_type)
    if report_type == ReportType.DAILY:
        return "day"
    if report_type == ReportType.WEEKLY:
        return "week"
    if report_type == ReportType.DOCTOR:
        return "14d"
    return report_type.value


def _aggregate_evidence(scope: DataScope, window_label: object | None) -> EvidenceRef:
    label = str(window_label or "window")
    return EvidenceRef(
        kind="aggregate",
        ref_id=f"{scope.user_id}:{scope.window_start.isoformat()}:{scope.window_end.isoformat()}:{label}",
        summary=f"{label} aggregate for {scope.window_start.isoformat()} to {scope.window_end.isoformat()}",
    )


def _event_evidence(event: UserEvent) -> EvidenceRef:
    state = "confirmed" if event.user_confirmed else "candidate"
    return EvidenceRef(
        kind="event",
        ref_id=event.event_id,
        summary=f"{state}: {event.event_type} at {event.ts_start.isoformat()}",
    )


def _coverage_confidence(data_coverage: float) -> float:
    if data_coverage >= 70:
        return 0.9
    if data_coverage > 0:
        return 0.55
    return 0.25


def _context_version(context: MemoryContext | AuthoritativeContext) -> str:
    if not context.enabled:
        return "disabled"
    if getattr(context, "missing_reason", None):
        return str(context.missing_reason)
    return "supplied" if (context.items if isinstance(context, MemoryContext) else context.documents) else "empty"


def _context_evidence_refs(items: list[dict[str, object]]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for item in items:
        for ref in item.get("evidence_refs", []) if isinstance(item, dict) else []:
            refs.append(EvidenceRef.model_validate(ref))
    return refs


def _unique_evidence_refs(refs: object) -> list[EvidenceRef]:
    unique: dict[tuple[str, str], EvidenceRef] = {}
    for ref in refs:
        parsed = EvidenceRef.model_validate(ref)
        unique[(str(parsed.kind), parsed.ref_id)] = parsed
    return list(unique.values())


def _unique_source_tracks(tracks: list[ReportSourceTrack]) -> list[ReportSourceTrack]:
    return list(dict.fromkeys(tracks))


def _output_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()
