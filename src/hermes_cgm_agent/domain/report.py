from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.domain.cgm import CGMBaseModel, DataScope, EvidenceRef, ensure_utc, utc_now


class ReportType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    DOCTOR = "doctor"


class ReportAudience(str, Enum):
    SELF = "self"
    CLINICIAN = "clinician"
    FAMILY = "family"


class ReportStatus(str, Enum):
    DRAFT = "draft"
    GENERATED = "generated"
    EXPORTED = "exported"
    SUPERSEDED = "superseded"


class ReportSourceTrack(str, Enum):
    FACT = "fact"
    USER_MEMORY = "user_memory"
    AUTHORITATIVE = "authoritative"
    MIXED = "mixed"


class DataQualitySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DataQualityWarning(CGMBaseModel):
    code: str
    message: str
    severity: DataQualitySeverity = DataQualitySeverity.WARNING
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class G8MemoryCandidate(CGMBaseModel):
    target_layer: str
    candidate_type: str
    summary: str
    source_report_id: str | None = None
    source_section_id: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    requires_user_confirmation: bool = True


class ReportSection(CGMBaseModel):
    section_id: str
    kind: str
    title: str
    content: str
    data_scope: DataScope
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    source_tracks: list[ReportSourceTrack] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[DataQualityWarning] = Field(default_factory=list)
    g8_memory_candidates: list[G8MemoryCandidate] = Field(default_factory=list)
    # D056: an empty low-signal section (no user events / no anomalies / no
    # pattern) is still built — so the memory-candidate pipeline and
    # section-existence contracts hold — but the renderer hides it from
    # everyday/family readers to keep the report short. Clinician sees all.
    omit_for_companion: bool = False


class FactsContext(CGMBaseModel):
    aggregate: dict[str, Any] | None = None
    points_summary: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    data_quality: list[DataQualityWarning] = Field(default_factory=list)


class MemoryContext(CGMBaseModel):
    enabled: bool = True
    items: list[dict[str, Any]] = Field(default_factory=list)
    missing_reason: str | None = None
    # D031: serialized ConflictResolution entries produced when a personal
    # numeric belief contradicts an authoritative KB range. Authoritative wins;
    # downstream sections must present the note gently, never as a denial.
    conflict_resolutions: list[dict[str, Any]] = Field(default_factory=list)


class AuthoritativeDocument(CGMBaseModel):
    title: str
    text: str = ""
    kb_version: str = ""
    source: str | None = None
    citation: dict[str, Any] = Field(default_factory=dict)
    verified: bool | None = None
    tier: str | None = None
    population: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class AuthoritativeContext(CGMBaseModel):
    enabled: bool = True
    documents: list[AuthoritativeDocument] = Field(default_factory=list)
    missing_reason: str | None = None


class ReportInput(CGMBaseModel):
    report_type: ReportType
    user_id: str | None = None
    audience: ReportAudience = ReportAudience.SELF
    data_scope: DataScope | None = None
    timezone: str = Field(default_factory=default_timezone)
    report_anchor_time: time = time(7, 0)
    anchor_at: datetime = Field(default_factory=utc_now)
    language: str | None = None
    # Optional retrieval filter for population-specific authoritative guidance
    # (e.g. pregnancy, pediatric, older/high-risk, inpatient).
    population: str | None = None
    memory_context: MemoryContext = Field(default_factory=MemoryContext)
    authoritative_context: AuthoritativeContext = Field(default_factory=AuthoritativeContext)
    # F3-B1 (US1): an externally-generated medical-claim/guidance narrative whose
    # numeric clinical figures MUST be backed by the retrieved authoritative
    # cards. When present it passes through the strict citation gate in
    # ReportService.generate before delivery (analyze I2/I3 — scoped to this
    # narrative only, never the user's own deterministic metric sections).
    medical_narrative: str | None = None
    include_candidate_events: bool = True
    consecutive_anomaly_days: int | None = None
    escalation_level: str | None = None
    # P1-5 (MVP audit): the user utterance that triggered this report, when
    # available. Affect detection runs on it — a distress hit makes the
    # builder lead with an empathy section before any data section
    # (emotional-first as code orchestration, not just a prompt promise).
    user_message: str | None = None

    @model_validator(mode="after")
    def normalize_anchor(self) -> "ReportInput":
        # Without this, a naive anchor_at from a Hermes tool call is
        # interpreted as *system-local* time by astimezone() in
        # resolve_report_scope, shifting the report window on machines whose
        # OS timezone differs from the user's.
        self.anchor_at = ensure_utc(self.anchor_at)
        return self

    @model_validator(mode="after")
    def validate_user_id(self) -> ReportInput:
        if self.data_scope is None and not self.user_id:
            raise ValueError("user_id is required when data_scope is not provided")
        if self.data_scope is not None and self.user_id is not None and self.data_scope.user_id != self.user_id:
            raise ValueError("user_id must match data_scope.user_id")
        return self


class Report(CGMBaseModel):
    report_id: str
    user_id: str
    report_type: ReportType
    audience: ReportAudience
    data_scope: DataScope
    timezone: str
    report_anchor_time: time
    generated_at: datetime = Field(default_factory=utc_now)
    status: ReportStatus = ReportStatus.GENERATED
    sections: list[ReportSection] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    data_quality_warnings: list[DataQualityWarning] = Field(default_factory=list)
    g8_memory_candidates: list[G8MemoryCandidate] = Field(default_factory=list)
    rendered_markdown: str = ""
    rendered_path: str | None = None
    audit_id: str | None = None
    source_versions: dict[str, Any] = Field(default_factory=dict)
    template_version: str = "g7-report-template-v1"
    output_hash: str = ""
    route: str = "reports.generate"
    safety_result: dict[str, Any] = Field(
        default_factory=lambda: {
            "status": "not_run",
            "reason": "safety_review_not_implemented",
        }
    )

    @model_validator(mode="after")
    def validate_report_user(self) -> Report:
        if self.data_scope.user_id != self.user_id:
            raise ValueError("data_scope.user_id must match report user_id")
        return self
