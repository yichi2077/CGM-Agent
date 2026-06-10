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


class EscalationState(str, Enum):
    NORMAL = "normal"
    CONCERN = "concern"
    EXTERNAL_SUPPORT = "external_support"

    @classmethod
    def derive(cls, consecutive_days: int, is_vulnerable: bool = False) -> EscalationState:
        if is_vulnerable:
            # D046/RC1 — vulnerable, earlier (SOUL.md "第一天/第三天/第五天"):
            # concern from day 1, external support from day 5.
            if consecutive_days >= 5:
                return cls.EXTERNAL_SUPPORT
            elif consecutive_days >= 1:
                return cls.CONCERN
            else:
                return cls.NORMAL
        else:
            # D046/RC1 — standard (SOUL.md 第一天 / 连续几天 / 一周):
            # concern from day 3, external support from about a week (day 7).
            if consecutive_days >= 7:
                return cls.EXTERNAL_SUPPORT
            elif consecutive_days >= 3:
                return cls.CONCERN
            else:
                return cls.NORMAL



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
    # Bi-temporal validity (D032): valid_to=None means currently valid; a
    # superseding belief closes the old one's window instead of deleting it.
    valid_from: datetime = Field(default_factory=utc_now)
    valid_to: datetime | None = None
    # Lineage (D032): the L1 episodes that support this belief.
    source_episode_ids: list[str] = Field(default_factory=list)
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
    # Bi-temporal validity + lineage (D032), mirroring L2.
    valid_from: datetime = Field(default_factory=utc_now)
    valid_to: datetime | None = None
    source_episode_ids: list[str] = Field(default_factory=list)
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


class PendingInteraction(CGMBaseModel):
    """F4 tracking for unanswered proactive pushes (e.g., missing data query).
    
    TTL is typically 3 days (Q2=B). If unresolved after expires_at, it's dropped.
    """
    interaction_id: str
    user_id: str
    interaction_type: str  # e.g., "missing_data_query", "insight_question"
    content: str
    is_active: bool = True
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    def check_active(self, now: datetime | None = None) -> bool:
        ref_time = now or utc_now()
        if self.resolved_at is not None:
            return False
        if ref_time >= self.expires_at:
            return False
        return self.is_active

    @property
    def is_expired(self) -> bool:
        return utc_now() >= self.expires_at



class MemorySummary(CGMBaseModel):
    """Warm-layer synthesized state ("dreaming", D034).

    A periodically regenerated, structured digest of the user's recent state
    (e.g., "本周 TIR 72%, 环比 +3%; 近期晚餐后偏高"). It is a derived product —
    cheap to recompute from raw data + memory — and is injected at prefetch.
    """

    summary_id: str
    user_id: str
    period: str  # "daily" | "weekly" | "monthly"
    window_start: datetime
    window_end: datetime
    content: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
