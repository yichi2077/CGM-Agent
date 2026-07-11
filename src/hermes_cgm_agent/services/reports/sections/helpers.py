from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import DataScope, EvidenceRef, UserEvent
from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.domain.report import (
    AuthoritativeDocument,
    AuthoritativeContext,
    DataQualityWarning,
    DataQualitySeverity,
    MemoryContext,
    ReportAudience,
    ReportSourceTrack,
    ReportType,
)


REPORT_WINDOW_DAYS = {
    ReportType.DAILY: 1,
    ReportType.WEEKLY: 7,
    ReportType.DOCTOR: 14,
}


@dataclass(frozen=True)
class PatternSignal:
    summaries: list[str]
    evidence_refs: list[EvidenceRef]
    emit_memory_candidates: bool


def resolve_report_scope(
    *,
    user_id: str,
    report_type: ReportType | str,
    timezone_name: str | None = None,
    anchor_time: time = time(7, 0),
    anchor_at: datetime | None = None,
) -> DataScope:
    if timezone_name is None:
        timezone_name = default_timezone()
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
    # Subtract in local calendar time before converting to UTC. A UTC-side
    # ``timedelta(days=...)`` makes a daily/weekly report one hour too wide or
    # too narrow across DST boundaries.
    window_start = (
        local_anchor - timedelta(days=REPORT_WINDOW_DAYS[parsed_type])
    ).astimezone(timezone.utc)
    window_end = local_anchor.astimezone(timezone.utc)
    return DataScope(
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
    )


def _display_glucose(value_mgdl: float | None) -> str:
    """Render a glucose value in the operator's display unit (D052/D053).

    SELF/FAMILY narrative only — clinician sections keep clinical mg/dL.
    Storage and analytics are unaffected.
    """
    from hermes_cgm_agent.config import display_glucose_unit
    from hermes_cgm_agent.domain import GlucoseUnit, convert_glucose_value

    if value_mgdl is None:
        return ""
    if display_glucose_unit() == "mmol/L":
        mmol = convert_glucose_value(float(value_mgdl), GlucoseUnit.MG_DL, GlucoseUnit.MMOL_L)
        return f"{round(mmol, 1)} mmol/L"
    return f"{value_mgdl} mg/dL"


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


def _context_evidence_refs(items: list[dict[str, object] | AuthoritativeDocument]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for item in items:
        if isinstance(item, dict):
            raw_refs = item.get("evidence_refs", [])
        else:
            raw_refs = item.evidence_refs
        for ref in raw_refs:
            refs.append(EvidenceRef.model_validate(ref))
    return refs


def _authoritative_context_warnings(
    documents: list[AuthoritativeDocument],
) -> list[DataQualityWarning]:
    unverified = [doc for doc in documents if doc.verified is False]
    if not unverified:
        return []
    details = "；".join(_authoritative_doc_label(doc) for doc in unverified)
    return [
        DataQualityWarning(
            code="authoritative_unverified",
            severity=DataQualitySeverity.WARNING,
            message=(
                "以下为指南摘录草稿，非医疗建议；以下医学参考仍待人工核验，"
                "仅可作为背景线索，不能作为最终医学依据："
                f"{details}"
            ),
            evidence_refs=_context_evidence_refs(unverified),
        )
    ]


def _authoritative_doc_label(doc: AuthoritativeDocument) -> str:
    label = doc.title
    if doc.population:
        label += f" [{doc.population}]"
    if doc.source:
        label += f" ({doc.source})"
    return label


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


def _event_type_label(event_type: str, audience: ReportAudience) -> str:
    labels = {
        "hypo": ("偏低片段", "低血糖事件", "偏低片段"),
        "hyper": ("偏高片段", "高血糖事件", "偏高片段"),
        "rapid_rise": ("上冲片段", "快速上升事件", "上冲片段"),
        "rapid_fall": ("回落片段", "快速下降事件", "回落片段"),
        "overnight_low": ("夜间偏低片段", "夜间低血糖事件", "夜间偏低片段"),
        "data_gap": ("记录空白片段", "数据缺口事件", "记录空白片段"),
    }
    self_label, clinician_label, family_label = labels.get(
        event_type,
        (event_type.replace("_", " "), event_type.replace("_", " "), event_type.replace("_", " ")),
    )
    if audience == ReportAudience.CLINICIAN:
        return clinician_label
    if audience == ReportAudience.FAMILY:
        return family_label
    return self_label
