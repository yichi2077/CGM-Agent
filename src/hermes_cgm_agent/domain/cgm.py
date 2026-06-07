from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

MGDL_PER_MMOLL = 18.0182


class CGMBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=True)


class GlucoseUnit(str, Enum):
    MMOL_L = "mmol/L"
    MG_DL = "mg/dL"


class GlucoseTrend(str, Enum):
    RISING_FAST = "rising_fast"
    RISING = "rising"
    STABLE = "stable"
    FALLING = "falling"
    FALLING_FAST = "falling_fast"
    UNKNOWN = "unknown"


class QualityFlag(str, Enum):
    VALID = "valid"
    WARMUP = "warmup"
    CALIBRATION = "calibration"
    GAP = "gap"
    SUSPECT = "suspect"


class SourceFormat(str, Enum):
    CSV = "csv"
    JSON = "json"
    DEVICE_EXPORT = "device_export"
    API = "api"
    MANUAL = "manual"


class EventType(str, Enum):
    MEAL = "meal"
    EXERCISE = "exercise"
    MEDICATION = "medication"
    SYMPTOM = "symptom"
    NOTE = "note"
    FEEDBACK = "feedback"
    CLINIC_FOLLOWUP = "clinic_followup"


class CreatedBy(str, Enum):
    USER = "user"
    AGENT = "agent"
    DEVICE = "device"


class WindowLabel(str, Enum):
    DAY = "day"
    WEEK = "week"
    FOURTEEN_DAYS = "14d"
    MONTH = "month"


class EvidenceKind(str, Enum):
    GLUCOSE_POINT = "glucose_point"
    AGGREGATE = "aggregate"
    EVENT = "event"
    MEMORY = "memory"
    DOCUMENT = "document"
    USER_MEMORY = "user_memory"
    AUTHORITATIVE_KB = "authoritative_kb"
    REPORT = "report"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def convert_glucose_value(value: float, from_unit: GlucoseUnit | str, to_unit: GlucoseUnit | str) -> float:
    source = GlucoseUnit(from_unit)
    target = GlucoseUnit(to_unit)
    if source == target:
        return value
    if source == GlucoseUnit.MMOL_L and target == GlucoseUnit.MG_DL:
        return value * MGDL_PER_MMOLL
    return value / MGDL_PER_MMOLL


class TimeRange(CGMBaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_order(self) -> TimeRange:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class DataScope(CGMBaseModel):
    user_id: str
    window_start: datetime
    window_end: datetime
    source: str | None = None

    @model_validator(mode="after")
    def validate_window(self) -> DataScope:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class EvidenceRef(CGMBaseModel):
    kind: EvidenceKind
    ref_id: str
    summary: str | None = None


class RawCGMRecord(CGMBaseModel):
    source_id: str
    source_format: SourceFormat
    raw_payload: dict[str, Any]
    row_number: int | None = Field(default=None, ge=1)
    recorded_at: datetime | None = None
    value: float | None = Field(default=None, gt=0)
    unit: GlucoseUnit | None = None
    device_id: str | None = None
    source_record_id: str | None = None


class ImportIssue(CGMBaseModel):
    row_number: int | None = Field(default=None, ge=1)
    field: str | None = None
    message: str
    raw_record: dict[str, Any] | None = None


class RawImportBatch(CGMBaseModel):
    batch_id: str
    source_name: str
    source_format: SourceFormat
    imported_at: datetime = Field(default_factory=utc_now)
    records: list[RawCGMRecord] = Field(default_factory=list)
    issues: list[ImportIssue] = Field(default_factory=list)

    @computed_field
    @property
    def record_count(self) -> int:
        return len(self.records)

    @computed_field
    @property
    def issue_count(self) -> int:
        return len(self.issues)


class GlucosePoint(CGMBaseModel):
    user_id: str
    timestamp: datetime
    value: float = Field(gt=0)
    unit: GlucoseUnit
    source: str
    quality_flag: QualityFlag
    trend: GlucoseTrend = GlucoseTrend.UNKNOWN
    device_id: str | None = None
    session_id: str | None = None
    raw_record_id: str | None = None

    @computed_field
    @property
    def value_mg_dl(self) -> float:
        return round(convert_glucose_value(self.value, self.unit, GlucoseUnit.MG_DL), 2)

    @computed_field
    @property
    def value_mmol_l(self) -> float:
        return round(convert_glucose_value(self.value, self.unit, GlucoseUnit.MMOL_L), 2)


class DeviceSession(CGMBaseModel):
    session_id: str
    user_id: str
    device_id: str
    sensor_started_at: datetime
    sensor_ended_at: datetime | None = None
    warmup_ended_at: datetime | None = None
    missing_ranges: list[TimeRange] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_session_order(self) -> DeviceSession:
        if self.sensor_ended_at is not None and self.sensor_ended_at <= self.sensor_started_at:
            raise ValueError("sensor_ended_at must be after sensor_started_at")
        if self.warmup_ended_at is not None and self.warmup_ended_at < self.sensor_started_at:
            raise ValueError("warmup_ended_at must be after sensor_started_at")
        return self


class UserEvent(CGMBaseModel):
    event_id: str
    user_id: str
    event_type: EventType = Field(alias="type")
    ts_start: datetime
    ts_end: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    attachment: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    created_by: CreatedBy
    user_confirmed: bool
    is_sensitive: bool = False
    is_rejected: bool = False

    @model_validator(mode="after")
    def validate_event_order(self) -> UserEvent:
        if self.ts_end is not None and self.ts_end <= self.ts_start:
            raise ValueError("ts_end must be after ts_start")
        return self


class GlucoseEventType(str, Enum):
    """Deterministically detected glucose events.

    These are derived observations over normalized facts, distinct from the
    user/agent-recorded ``UserEvent`` (see DECISION_LOG D022). They never carry a
    user-confirmation flag because they are characteristics of the data, not user
    claims.
    """

    HYPO = "hypo"
    HYPER = "hyper"
    RAPID_RISE = "rapid_rise"
    RAPID_FALL = "rapid_fall"
    OVERNIGHT_LOW = "overnight_low"
    DATA_GAP = "data_gap"


class GlucoseEventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"


class GlucoseEvent(CGMBaseModel):
    event_id: str
    user_id: str
    event_type: GlucoseEventType
    ts_start: datetime
    ts_end: datetime
    severity: GlucoseEventSeverity = GlucoseEventSeverity.INFO
    peak_value_mg_dl: float | None = None
    nadir_value_mg_dl: float | None = None
    duration_minutes: float = Field(ge=0)
    point_count: int = Field(default=0, ge=0)
    summary: str
    detector_version: str = "g6-detector-v1"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_event_window(self) -> GlucoseEvent:
        if self.ts_end < self.ts_start:
            raise ValueError("ts_end must be at or after ts_start")
        return self


class GlucoseAggregate(CGMBaseModel):
    user_id: str
    window_start: datetime
    window_end: datetime
    window_label: WindowLabel | None = None
    tir: float | None = Field(default=None, alias="TIR", ge=0, le=100)
    tar: float | None = Field(default=None, alias="TAR", ge=0, le=100)
    tbr: float | None = Field(default=None, alias="TBR", ge=0, le=100)
    gmi: float | None = Field(default=None, alias="GMI", ge=0)
    cv: float | None = Field(default=None, alias="CV", ge=0)
    mbg: float | None = Field(default=None, alias="MBG", ge=0)
    lbgi: float | None = Field(default=None, alias="LBGI", ge=0)
    hbgi: float | None = Field(default=None, alias="HBGI", ge=0)
    mage: float | None = Field(default=None, alias="MAGE", ge=0)
    modd: float | None = Field(default=None, alias="MODD", ge=0)
    conga1: float | None = Field(default=None, alias="CONGA1", ge=0)
    conga2: float | None = Field(default=None, alias="CONGA2", ge=0)
    conga4: float | None = Field(default=None, alias="CONGA4", ge=0)
    data_coverage: float = Field(ge=0, le=100)
    point_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_aggregate_window(self) -> GlucoseAggregate:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self
