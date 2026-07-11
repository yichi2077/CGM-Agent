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
from numbers import Real
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
        audit_service: Any | None = None,
    ) -> None:
        self.repository = repository
        # M-27: inject audit_service into ConsolidationService so domain-level
        # consolidation events are properly audited.
        self.consolidation = consolidation or ConsolidationService(
            repository=repository, audit_service=audit_service
        )

    def ingest_report_candidates(
        self,
        candidates: list[MemoryCandidate],
        *,
        now: datetime | None = None,
    ) -> IngestResult:
        now = now or _now()
        auto = 0
        pending = 0
        # C-03: deduplicate against already-queued candidates so re-running
        # a report (same deterministic candidate_id) does not crash with
        # IntegrityError.  Mirrors the check in provider.sync_turn.
        # Cycle2: check inside per-candidate transaction to close TOCTOU window.
        user_ids = {c.user_id for c in candidates}
        existing_ids: set[str] = set()
        for uid in user_ids:
            existing_ids.update(
                c.candidate_id for c in self.repository.list_candidates(uid)
            )
        for candidate in candidates:
            if candidate.candidate_id in existing_ids:
                continue
            if not candidate.requires_user_confirmation:
                # C-04: wrap auto-accept (enqueue + accept + status update)
                # in a transaction so a crash between steps cannot leave
                # an L1 episode persisted while the candidate stays PENDING.
                try:
                    with self.repository.store.transaction():
                        # Cycle2: re-check inside transaction to close TOCTOU.
                        current = self.repository.list_candidates(candidate.user_id)
                        if any(c.candidate_id == candidate.candidate_id for c in current):
                            existing_ids.add(candidate.candidate_id)
                            continue
                        self.repository.enqueue_candidate(candidate)
                        self._accept(candidate, now=now)
                        self.repository.set_candidate_status(
                            candidate.candidate_id, status=CandidateStatus.ACCEPTED, when=now
                        )
                except Exception as exc:
                    # Cycle2: only swallow duplicate-key IntegrityError; let
                    # other exceptions (e.g. RuntimeError from status update)
                    # propagate so callers can handle them.
                    if "integrity" in str(exc).lower() or "unique" in str(exc).lower():
                        existing_ids.add(candidate.candidate_id)
                        continue
                    raise
                auto += 1
            else:
                self.repository.enqueue_candidate(candidate)
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
        # The TTL is a safety boundary, not only a periodic housekeeping task:
        # confirmation may arrive before the next consolidation run.  Keep the
        # purge, promotion, and state transition in one transaction so an
        # expired candidate cannot be promoted into durable L1 memory.
        with self.repository.store.transaction():
            self.repository.purge_expired_candidates(now=now)
            pending = {
                c.candidate_id: c
                for c in self.repository.list_candidates(user_id)
            }
            candidate = pending.get(candidate_id)
            if candidate is None:
                raise KeyError(f"Unknown or expired candidate: {candidate_id}")
            if candidate.status != CandidateStatus.PENDING:
                raise ValueError(f"Candidate already resolved: {candidate_id}")
            if confirmed:
                # C4: promote first, then mark ACCEPTED only after the L1 write
                # durably succeeds. If _accept raises, the candidate stays
                # PENDING so the confirmation can be retried.
                self._accept(candidate, now=now)
                return self.repository.set_candidate_status(
                    candidate_id, status=CandidateStatus.ACCEPTED, when=now
                )
            return self.repository.set_candidate_status(
                candidate_id, status=CandidateStatus.REJECTED, when=now
            )

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
            episode.summary = _require_string(correction["summary"], "correction.summary")
        if "confidence" in correction:
            episode.confidence = _require_number(correction["confidence"], "correction.confidence")
        if "archive" in correction:
            episode.is_archived = _require_bool(correction["archive"], "correction.archive")
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
            item.value = _require_object(correction["value"], "correction.value")
        if "confidence" in correction:
            item.confidence = _require_number(correction["confidence"], "correction.confidence")
        if "deactivate" in correction:
            item.is_active = not _require_bool(correction["deactivate"], "correction.deactivate")
        item.last_verified = now
        item.updated_at = now
        self.repository.upsert_profile_item(item)
        return item.item_id

    def _correct_l3(self, user_id: str, correction: dict[str, Any], now: datetime) -> str | None:
        hyp_id = correction.get("hypothesis_id")
        # M-08: active_only=False so archived hypotheses can also be corrected.
        hyps = {h.hypothesis_id: h for h in self.repository.list_hypotheses(user_id, active_only=False)}
        hyp = hyps.get(hyp_id) if hyp_id else None
        if hyp is None:
            return None
        if "statement" in correction:
            hyp.statement = _require_string(correction["statement"], "correction.statement")
        if "state" in correction:
            hyp.state = HypothesisState(
                _require_enum(
                    correction["state"],
                    "correction.state",
                    ("candidate", "observing", "stable", "archived"),
                )
            )
        hyp.last_checked = now
        hyp.updated_at = now
        self.repository.upsert_hypothesis(hyp)
        return hyp.hypothesis_id


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _require_enum(value: Any, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(allowed)}")
    return value
