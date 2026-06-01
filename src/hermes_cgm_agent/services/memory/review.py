"""Memory candidate review + correction (MEM-ARCH-20260601 §5; D026).

Bridges G7 report `g8_memory_candidates` into the G8 memory store:
- ingest: enqueue candidates; auto-accept + promote those with
  requires_user_confirmation == False; others stay pending for explicit review.
- confirm: user accepts/rejects a pending candidate (accept -> promote to L1).
- correct: user corrects an existing L1/L2/L3 record.

Promotion writes an L1 episode; periodic L1->L2->L3 consolidation is the
ConsolidationService's job (separate, async). Nothing is activated as durable
memory without either requires_user_confirmation == False or explicit confirm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hermes_cgm_agent.domain import (
    CandidateStatus,
    HypothesisState,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.services.memory.consolidation import ConsolidationService
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository


@dataclass(frozen=True)
class IngestResult:
    enqueued: int
    auto_accepted: int
    pending: int


class MemoryReviewService:
    def __init__(
        self,
        *,
        repository: SQLiteMemoryRepository,
        consolidation: ConsolidationService | None = None,
    ) -> None:
        self.repository = repository
        self.consolidation = consolidation or ConsolidationService(repository=repository)

    def ingest_report_candidates(
        self,
        candidates: list[MemoryCandidate],
        *,
        now: datetime | None = None,
    ) -> IngestResult:
        now = now or _now()
        auto = 0
        pending = 0
        for candidate in candidates:
            self.repository.enqueue_candidate(candidate)
            if not candidate.requires_user_confirmation:
                self._accept(candidate, now=now)
                self.repository.set_candidate_status(
                    candidate.candidate_id, status=CandidateStatus.ACCEPTED, when=now
                )
                auto += 1
            else:
                pending += 1
        return IngestResult(enqueued=len(candidates), auto_accepted=auto, pending=pending)

    def confirm_candidate(
        self,
        candidate_id: str,
        *,
        user_id: str,
        confirmed: bool,
        now: datetime | None = None,
    ) -> MemoryCandidate:
        now = now or _now()
        pending = {c.candidate_id: c for c in self.repository.list_candidates(user_id)}
        candidate = pending.get(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate: {candidate_id}")
        if candidate.status != CandidateStatus.PENDING:
            raise ValueError(f"Candidate already resolved: {candidate_id}")
        if confirmed:
            # C4: promote first, then mark ACCEPTED only after the L1 write
            # durably succeeds. If _accept raises, the candidate stays PENDING so
            # the confirmation can be retried (the old order left it ACCEPTED
            # with no memory record and blocked retry via the resolved guard).
            self._accept(candidate, now=now)
            resolved = self.repository.set_candidate_status(
                candidate_id, status=CandidateStatus.ACCEPTED, when=now
            )
        else:
            resolved = self.repository.set_candidate_status(
                candidate_id, status=CandidateStatus.REJECTED, when=now
            )
        return resolved

    def correct(
        self,
        *,
        user_id: str,
        target: MemoryLayer | str,
        correction: dict[str, Any],
        now: datetime | None = None,
    ) -> str | None:
        """Apply an explicit user correction to an existing memory record.

        correction must include the target id and the fields to change.
        Returns the corrected record id, or None if not found.
        """
        now = now or _now()
        layer = MemoryLayer(target)
        if layer == MemoryLayer.L1:
            return self._correct_l1(user_id, correction, now)
        if layer == MemoryLayer.L2:
            return self._correct_l2(user_id, correction, now)
        return self._correct_l3(user_id, correction, now)

    # -- internals -----------------------------------------------------------

    def _accept(self, candidate: MemoryCandidate, *, now: datetime) -> None:
        """Promote an accepted candidate into its target layer.

        L1 -> episode; L2/L3 promotions go through consolidation thresholds, so
        for now an accepted L2/L3 candidate is recorded as an L1 episode that
        consolidation can later aggregate (avoids treating one report line as a
        stable profile/hypothesis, per D026)."""
        episode_type = candidate.candidate_type or "episode"
        self.consolidation.ingest_accepted_candidate(
            candidate,
            occurred_at=now,
            episode_type=episode_type,
            now=now,
        )

    def _correct_l1(self, user_id: str, correction: dict[str, Any], now: datetime) -> str | None:
        episode_id = correction.get("episode_id")
        if not episode_id:
            raise ValueError("correction.episode_id is required for L1 correction")
        episodes = {e.episode_id: e for e in self.repository.list_episodes(user_id, include_archived=True)}
        episode = episodes.get(episode_id)
        if episode is None:
            return None
        if "summary" in correction:
            episode.summary = correction["summary"]
        if "confidence" in correction:
            episode.confidence = float(correction["confidence"])
        if correction.get("archive"):
            episode.is_archived = True
        episode.last_referenced_at = now
        self.repository.replace_episode(episode)
        return episode_id

    def _correct_l2(self, user_id: str, correction: dict[str, Any], now: datetime) -> str | None:
        item_id = correction.get("item_id")
        items = {i.item_id: i for i in self.repository.list_profile_items(user_id, active_only=False)}
        item = items.get(item_id) if item_id else None
        if item is None:
            return None
        if "value" in correction:
            item.value = correction["value"]
        if "confidence" in correction:
            item.confidence = float(correction["confidence"])
        if "deactivate" in correction:
            item.is_active = not bool(correction["deactivate"])
        item.last_verified = now
        item.updated_at = now
        self.repository.upsert_profile_item(item)
        return item.item_id

    def _correct_l3(self, user_id: str, correction: dict[str, Any], now: datetime) -> str | None:
        hyp_id = correction.get("hypothesis_id")
        hyps = {h.hypothesis_id: h for h in self.repository.list_hypotheses(user_id)}
        hyp = hyps.get(hyp_id) if hyp_id else None
        if hyp is None:
            return None
        if "statement" in correction:
            hyp.statement = correction["statement"]
        if "state" in correction:
            hyp.state = HypothesisState(correction["state"])
        hyp.last_checked = now
        hyp.updated_at = now
        self.repository.upsert_hypothesis(hyp)
        return hyp.hypothesis_id


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
