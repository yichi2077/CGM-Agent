from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    CandidateStatus,
    EvidenceRef,
    HypothesisState,
    L1Episode,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.services.memory import (
    ConsolidationConfig,
    ConsolidationService,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class ConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        store.initialize()
        self.repo = SQLiteMemoryRepository(store)
        self.svc = ConsolidationService(repository=self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ingest_accepted_candidate_creates_l1(self) -> None:
        cand = MemoryCandidate(
            candidate_id="c1",
            user_id="u1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Lunch spike",
            evidence_refs=[EvidenceRef(kind="event", ref_id="ev1")],
            confidence=0.8,
        )
        episode = self.svc.ingest_accepted_candidate(
            cand, occurred_at=NOW, episode_type="postprandial_spike", now=NOW
        )
        stored = self.repo.list_episodes("u1")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].episode_id, episode.episode_id)
        self.assertEqual(stored[0].episode_type, "postprandial_spike")

    def test_recurrence_promotes_to_l2_belief_and_l3_hypothesis(self) -> None:
        # 3 distinct days of the same hyper episode type.
        for d in range(3):
            self._episode("hyper", NOW - timedelta(days=d))

        report = self.svc.consolidate("u1", now=NOW)

        beliefs = self.repo.list_profile_items("u1")
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(report.profiles_updated, 1)
        self.assertEqual(beliefs[0].key, "pattern:hyper")
        self.assertEqual(beliefs[0].evidence_count, 3)
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)

    def test_strong_recurrence_marks_hypothesis_stable(self) -> None:
        for d in range(5):
            self._episode("overnight_low", NOW - timedelta(days=d))

        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")

        self.assertEqual(hyps[0].state, HypothesisState.STABLE)
        self.assertEqual(hyps[0].evidence_count, 5)

    def test_single_episode_does_not_promote(self) -> None:
        self._episode("hypo", NOW)
        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.profiles_updated, 0)
        self.assertEqual(report.hypotheses_updated, 0)
        self.assertEqual(self.repo.list_profile_items("u1"), [])

    def test_consolidate_archives_stale_l1(self) -> None:
        old = self._episode("note", NOW - timedelta(days=200))
        old.last_referenced_at = NOW - timedelta(days=120)
        # rewrite with stale ref time
        self.repo.touch_episode(old.episode_id, when=NOW - timedelta(days=120))

        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.episodes_archived, 1)
        self.assertEqual(self.repo.list_episodes("u1"), [])

    def _episode(self, episode_type: str, occurred_at: datetime) -> L1Episode:
        ep = L1Episode(
            episode_id=new_id(),
            user_id="u1",
            occurred_at=occurred_at,
            episode_type=episode_type,
            summary=f"{episode_type} at {occurred_at.isoformat()}",
            evidence_refs=[EvidenceRef(kind="event", ref_id=f"ev-{occurred_at.date()}")],
            confidence=0.7,
            created_at=occurred_at,
            last_referenced_at=occurred_at,
        )
        return self.repo.create_episode(ep)


if __name__ == "__main__":
    unittest.main()
