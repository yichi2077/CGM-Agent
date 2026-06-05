"""L1/L2/L3 memory + memory-candidate domain models (MEM-ARCH-20260601 §5.1).

These are persisted records (unlike the transient L0 context and detected
GlucoseEvent). They are carried by the self-built CGMMemoryProvider (L1+L3) and
the USER.md-mapped L2 snapshot (DECISION_LOG D012/D024-D026).

- L1Episode      : raw episodic memory (events + attribution), evidence-backed.
- L2ProfileItem  : distilled semantic belief, with confidence + last_verified +
                   evidence_count (Hindsight-style proof count) + decay support.
- L3Hypothesis   : individualized hypothesis state machine
                   (candidate -> observing -> stable / archived).
- MemoryCandidate: pending queue fed by G7 report `g8_memory_candidates`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from hermes_cgm_agent.domain.cgm import CGMBaseModel, EvidenceRef, utc_now


class MemoryLayer(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class HypothesisState(str, Enum):
    CANDIDATE = "candidate"
    OBSERVING = "observing"
    STABLE = "stable"
    ARCHIVED = "archived"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class L1Episode(CGMBaseModel):
    episode_id: str
    user_id: str
    occurred_at: datetime
    episode_type: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    source_report_id: str | None = None
    source_section_id: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    is_archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    last_referenced_at: datetime = Field(default_factory=utc_now)


class L2ProfileItem(CGMBaseModel):
    item_id: str
    user_id: str
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_count: int = Field(default=0, ge=0)
    last_verified: datetime = Field(default_factory=utc_now)
    supersedes_item_id: str | None = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class L3Hypothesis(CGMBaseModel):
    hypothesis_id: str
    user_id: str
    statement: str
    state: HypothesisState = HypothesisState.CANDIDATE
    evidence_count: int = Field(default=0, ge=0)
    contra_count: int = Field(default=0, ge=0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    last_checked: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MemoryCandidate(CGMBaseModel):
    candidate_id: str
    user_id: str
    target_layer: MemoryLayer
    candidate_type: str
    summary: str
    requires_user_confirmation: bool = True
    status: CandidateStatus = CandidateStatus.PENDING
    source_report_id: str | None = None
    source_section_id: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
