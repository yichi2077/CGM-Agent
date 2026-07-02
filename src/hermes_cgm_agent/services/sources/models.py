from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from hermes_cgm_agent.domain import GlucoseTrend, GlucoseUnit, ImportIssue

SourceKind = Literal["xdrip", "juggluco", "nightscout"]


@dataclass(frozen=True)
class SourceReading:
    measured_at: datetime
    value: float
    unit: GlucoseUnit
    trend: GlucoseTrend = GlucoseTrend.UNKNOWN
    device_id: str | None = None
    source_record_id: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedSourcePayload:
    readings: list[SourceReading]
    issues: list[ImportIssue]


@dataclass(frozen=True)
class SourcePollResult:
    user_id: str
    kind: SourceKind
    url: str
    source: str
    batch_id: str
    fetched_count: int
    parsed_count: int
    inserted_count: int
    duplicate_count: int
    issue_count: int
    detected_event_count: int
    detected_event_inserted: int
    detected_event_duplicate: int
    received_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "user_id": self.user_id,
            "kind": self.kind,
            "url": self.url,
            "source": self.source,
            "batch_id": self.batch_id,
            "fetched_count": self.fetched_count,
            "parsed_count": self.parsed_count,
            "inserted_count": self.inserted_count,
            "duplicate_count": self.duplicate_count,
            "issue_count": self.issue_count,
            "detected_event_count": self.detected_event_count,
            "detected_event_inserted": self.detected_event_inserted,
            "detected_event_duplicate": self.detected_event_duplicate,
            "received_at": self.received_at.isoformat(),
        }
