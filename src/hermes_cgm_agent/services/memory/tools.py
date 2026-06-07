from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cgm_agent.domain import (
    CandidateStatus,
    EvidenceRef,
    G8MemoryCandidate,
    HypothesisState,
    L3Hypothesis,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.arguments import require_enum
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.review import MemoryReviewService
from hermes_cgm_agent.services.memory.user_md_sync import UserMDSyncService


@dataclass(frozen=True)
class MemoryListResult:
    memories: list[dict[str, Any]]
    candidates: list[dict[str, Any]]

    @property
    def total_count(self) -> int:
        return len(self.memories)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


class MemoryToolService:
    """Business logic behind memory.* tools, separate from audit/response wiring."""

    def __init__(self, repository: SQLiteMemoryRepository) -> None:
        self.repository = repository

    def list_records(
        self,
        *,
        user_id: str,
        layer: str,
        include_archived: bool,
        candidate_status: CandidateStatus | None,
        limit: int | None,
    ) -> MemoryListResult:
        if layer == "candidates":
            memories: list[dict[str, Any]] = []
        else:
            memories = self._list_memories(
                user_id=user_id,
                layer=layer,
                include_archived=include_archived,
            )
        if limit is not None:
            memories = memories[:limit]
        candidates = self._list_candidates(
            user_id=user_id,
            layer=layer,
            status=candidate_status,
            limit=limit,
        )
        return MemoryListResult(memories=memories, candidates=candidates)

    def delete_record(self, *, user_id: str, memory_id: str, layer: str) -> bool:
        if layer == "L1":
            episode = self.repository.get_episode(memory_id)
            if episode is None or episode.user_id != user_id:
                return False
            return self.repository.delete_episode(memory_id)
        if layer == "L2":
            items = {
                item.item_id: item
                for item in self.repository.list_profile_items(user_id, active_only=False)
            }
            if memory_id not in items:
                return False
            return self.repository.delete_profile_item(memory_id)
        if layer == "L3":
            hypotheses = {
                item.hypothesis_id: item
                for item in self.repository.list_hypotheses(
                    user_id,
                    states=[
                        HypothesisState.CANDIDATE,
                        HypothesisState.OBSERVING,
                        HypothesisState.STABLE,
                        HypothesisState.ARCHIVED,
                    ],
                )
            }
            if memory_id not in hypotheses:
                return False
            return self.repository.delete_hypothesis(memory_id)
        raise ValueError("layer must be one of: L1, L2, L3")

    def confirm_candidate(self, *, user_id: str, candidate_id: str, confirmed: bool) -> str:
        resolved = MemoryReviewService(repository=self.repository).confirm_candidate(
            candidate_id,
            user_id=user_id,
            confirmed=confirmed,
        )
        return getattr(resolved.status, "value", resolved.status)

    def ingest_report_candidates(self, *, report: Any, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {
                "enabled": False,
                "enqueued": 0,
                "auto_accepted": 0,
                "pending": 0,
            }
        candidates = [
            _report_candidate_to_memory_candidate(report, candidate, index)
            for index, candidate in enumerate(report.g8_memory_candidates, start=1)
        ]
        if not candidates:
            return {
                "enabled": True,
                "enqueued": 0,
                "auto_accepted": 0,
                "pending": 0,
            }
        result = MemoryReviewService(repository=self.repository).ingest_report_candidates(candidates)
        return {
            "enabled": True,
            "enqueued": result.enqueued,
            "auto_accepted": result.auto_accepted,
            "pending": result.pending,
        }

    def correct_memory(
        self,
        *,
        user_id: str,
        target: str,
        correction: dict[str, Any],
        hermes_home: str | None = None,
    ) -> str | None:
        memory_id = MemoryReviewService(repository=self.repository).correct(
            user_id=user_id,
            target=target,
            correction=correction,
        )
        if memory_id and target == "L2" and hermes_home:
            UserMDSyncService(repository=self.repository).sync(
                user_id=user_id,
                hermes_home=hermes_home,
            )
        return memory_id

    def update_hypothesis(
        self,
        *,
        user_id: str,
        hypothesis_id: str,
        state: Any,
        evidence_refs: Any = None,
    ) -> L3Hypothesis:
        state_value = require_enum(
            state,
            "state",
            ("candidate", "observing", "stable", "archived"),
        )
        raw_refs = [] if evidence_refs is None else evidence_refs
        if not isinstance(raw_refs, list):
            raise ValueError("evidence_refs must be a list when provided")
        parsed_refs = [EvidenceRef.model_validate(ref) for ref in raw_refs]
        hypotheses = {
            item.hypothesis_id: item
            for item in self.repository.list_hypotheses(
                user_id,
                states=[
                    HypothesisState.CANDIDATE,
                    HypothesisState.OBSERVING,
                    HypothesisState.STABLE,
                    HypothesisState.ARCHIVED,
                ],
            )
        }
        hypothesis = hypotheses.get(hypothesis_id)
        if hypothesis is None:
            raise KeyError(f"Unknown hypothesis: {hypothesis_id}")
        hypothesis.state = HypothesisState(state_value)
        if parsed_refs:
            # Merge new evidence; keep the existing proof count monotonic.
            hypothesis.evidence_refs = [*hypothesis.evidence_refs, *parsed_refs]
            hypothesis.evidence_count = len(hypothesis.evidence_refs)
        now = utc_now()
        hypothesis.last_checked = now
        hypothesis.updated_at = now
        return self.repository.upsert_hypothesis(hypothesis)

    def _list_memories(
        self,
        *,
        user_id: str,
        layer: str,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        memories: list[dict[str, Any]] = []
        if layer in {"L1", "all"}:
            for episode in self.repository.list_episodes(user_id, include_archived=include_archived):
                item = episode.model_dump(mode="json")
                item["layer"] = "L1"
                item["memory_id"] = episode.episode_id
                memories.append(item)
        if layer in {"L2", "all"}:
            for profile in self.repository.list_profile_items(user_id, active_only=not include_archived):
                item = profile.model_dump(mode="json")
                item["layer"] = "L2"
                item["memory_id"] = profile.item_id
                memories.append(item)
        if layer in {"L3", "all"}:
            states = None if include_archived else [
                HypothesisState.CANDIDATE,
                HypothesisState.OBSERVING,
                HypothesisState.STABLE,
            ]
            for hypothesis in self.repository.list_hypotheses(user_id, states=states):
                item = hypothesis.model_dump(mode="json")
                item["layer"] = "L3"
                item["memory_id"] = hypothesis.hypothesis_id
                memories.append(item)
        return memories

    def _list_candidates(
        self,
        *,
        user_id: str,
        layer: str,
        status: CandidateStatus | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        if layer not in {"all", "candidates"}:
            return []
        candidates = self.repository.list_candidates(user_id, status=status)
        if limit is not None:
            candidates = candidates[:limit]
        return [candidate.model_dump(mode="json") for candidate in candidates]


def _report_candidate_to_memory_candidate(
    report: Any,
    candidate: G8MemoryCandidate,
    index: int,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=f"report-{report.report_id}-{index}",
        user_id=report.user_id,
        target_layer=MemoryLayer(candidate.target_layer),
        candidate_type=candidate.candidate_type,
        summary=candidate.summary,
        requires_user_confirmation=candidate.requires_user_confirmation,
        source_report_id=candidate.source_report_id or report.report_id,
        source_section_id=candidate.source_section_id,
        evidence_refs=candidate.evidence_refs,
        confidence=candidate.confidence,
    )
