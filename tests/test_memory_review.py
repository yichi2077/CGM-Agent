from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    CandidateStatus,
    EvidenceRef,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import MemoryReviewService, SQLiteMemoryRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class MemoryReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repo = SQLiteMemoryRepository(self.store)
        self.review = MemoryReviewService(repository=self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ingest_auto_accepts_unconfirmed_and_queues_rest(self) -> None:
        auto = self._candidate("c-auto", requires_confirmation=False)
        pend = self._candidate("c-pend", requires_confirmation=True)

        result = self.review.ingest_report_candidates([auto, pend], now=NOW)

        self.assertEqual(result.enqueued, 2)
        self.assertEqual(result.auto_accepted, 1)
        self.assertEqual(result.pending, 1)
        # auto-accepted candidate promoted to an L1 episode
        episodes = self.repo.list_episodes("u1")
        self.assertEqual(len(episodes), 1)
        # the other remains pending for explicit review
        pending = self.repo.list_candidates("u1", status=CandidateStatus.PENDING)
        self.assertEqual([c.candidate_id for c in pending], ["c-pend"])

    def test_confirm_promotes_pending_candidate(self) -> None:
        cand = self._candidate("c1", requires_confirmation=True)
        self.review.ingest_report_candidates([cand], now=NOW)

        resolved = self.review.confirm_candidate("c1", user_id="u1", confirmed=True, now=NOW)

        self.assertEqual(resolved.status, CandidateStatus.ACCEPTED)
        self.assertEqual(len(self.repo.list_episodes("u1")), 1)

    def test_reject_does_not_promote(self) -> None:
        cand = self._candidate("c1", requires_confirmation=True)
        self.review.ingest_report_candidates([cand], now=NOW)

        resolved = self.review.confirm_candidate("c1", user_id="u1", confirmed=False, now=NOW)

        self.assertEqual(resolved.status, CandidateStatus.REJECTED)
        self.assertEqual(self.repo.list_episodes("u1"), [])

    def test_memory_confirm_tool_path(self) -> None:
        cand = self._candidate("c1", requires_confirmation=True)
        self.review.ingest_report_candidates([cand], now=NOW)
        executor = self._executor()

        session = self.store.create_session(title="memory-test")
        body = executor.execute(
            tool_name="memory.confirm",
            arguments={"user_id": "u1", "candidate_id": "c1", "confirmed": True},
            session_id=session.id,
        ).to_dict()

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["candidate_status"], "accepted")
        self.assertIsNotNone(body["audit_id"])
        self.assertEqual(len(self.repo.list_episodes("u1")), 1)

    def test_memory_correct_tool_path_l1(self) -> None:
        cand = self._candidate("c1", requires_confirmation=False)
        self.review.ingest_report_candidates([cand], now=NOW)
        episode = self.repo.list_episodes("u1")[0]
        executor = self._executor()

        session = self.store.create_session(title="memory-test")
        body = executor.execute(
            tool_name="memory.correct",
            arguments={
                "user_id": "u1",
                "target": "L1",
                "correction": {"episode_id": episode.episode_id, "summary": "corrected"},
            },
            session_id=session.id,
        ).to_dict()

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["memory_id"], episode.episode_id)
        self.assertEqual(self.repo.list_episodes("u1")[0].summary, "corrected")

    def _candidate(self, candidate_id: str, *, requires_confirmation: bool) -> MemoryCandidate:
        return MemoryCandidate(
            candidate_id=candidate_id,
            user_id="u1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary=f"candidate {candidate_id}",
            requires_user_confirmation=requires_confirmation,
            evidence_refs=[EvidenceRef(kind="event", ref_id=f"ev-{candidate_id}")],
            confidence=0.8,
            created_at=NOW,
        )

    def _executor(self) -> ToolExecutor:
        return ToolExecutor(
            repository=SQLiteCGMRepository(self.store),
            audit_service=AuditService(self.store),
        )


if __name__ == "__main__":
    unittest.main()
