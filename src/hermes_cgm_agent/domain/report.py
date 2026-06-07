from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from hermes_cgm_agent.domain.cgm import CGMBaseModel, DataScope, EvidenceRef, utc_now


class ReportType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
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
    occurred_at: datetime | None = None
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


class FactsContext(CGMBaseModel):
    aggregate: dict[str, Any] | None = None
    points_summary: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    data_quality: list[DataQualityWarning] = Field(default_factory=list)


class MemoryContext(CGMBaseModel):
    enabled: bool = True
    items: list[dict[str, Any]] = Field(default_factory=list)
    missing_reason: str | None = None


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
    timezone: str = "Asia/Shanghai"
    report_anchor_time: time = time(7, 0)
    anchor_at: datetime = Field(default_factory=utc_now)
    language: str | None = None
    memory_context: MemoryContext = Field(default_factory=MemoryContext)
    authoritative_context: AuthoritativeContext = Field(default_factory=AuthoritativeContext)
    include_candidate_events: bool = True

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
