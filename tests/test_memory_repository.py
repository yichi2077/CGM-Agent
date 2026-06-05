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
    L2ProfileItem,
    L3Hypothesis,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository, new_id
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class MemoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        store.initialize()
        self.repo = SQLiteMemoryRepository(store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_l1_episode_roundtrip_and_time_filter(self) -> None:
        self.repo.create_episode(self._episode("e1", NOW - timedelta(days=1), "hypo"))
        self.repo.create_episode(self._episode("e2", NOW - timedelta(days=10), "hyper"))

        recent = self.repo.list_episodes("user-1", since=NOW - timedelta(days=2))
        hypers = self.repo.list_episodes("user-1", episode_type="hyper")

        self.assertEqual([e.episode_id for e in recent], ["e1"])
        self.assertEqual([e.episode_id for e in hypers], ["e2"])
        self.assertTrue(recent[0].evidence_refs)

    def test_l1_archive_stale_episodes(self) -> None:
        old = self._episode("e-old", NOW - timedelta(days=200), "note")
        old.last_referenced_at = NOW - timedelta(days=120)
        self.repo.create_episode(old)
        self.repo.create_episode(self._episode("e-new", NOW, "note"))

        archived = self.repo.archive_stale_episodes(now=NOW, max_idle_days=90)

        self.assertEqual(archived, 1)
        active = self.repo.list_episodes("user-1")
        self.assertEqual([e.episode_id for e in active], ["e-new"])
        self.assertEqual(len(self.repo.list_episodes("user-1", include_archived=True)), 2)

    def test_l2_upsert_and_decay(self) -> None:
        item = L2ProfileItem(
            item_id="carb",
            user_id="user-1",
            key="carb_sensitivity",
            value={"level": "high"},
            confidence=0.45,
            evidence_count=3,
            last_verified=NOW - timedelta(days=40),
        )
        self.repo.upsert_profile_item(item)

        changed = self.repo.decay_profile_items(now=NOW, stale_days=30, decay=0.2, deactivate_below=0.3)
        active = self.repo.list_profile_items("user-1")

        self.assertEqual(changed, 1)
        # 0.45 - 0.2 = 0.25 < 0.3 -> deactivated, dropped from active list
        self.assertEqual(active, [])
        all_items = self.repo.list_profile_items("user-1", active_only=False)
        self.assertAlmostEqualConfidence(all_items[0].confidence, 0.25)

    def test_l3_hypothesis_state_machine(self) -> None:
        hyp = L3Hypothesis(
            hypothesis_id="h1",
            user_id="user-1",
            statement="Friday dinners run high",
            state=HypothesisState.CANDIDATE,
            evidence_count=1,
        )
        self.repo.upsert_hypothesis(hyp)
        hyp.state = HypothesisState.OBSERVING
        hyp.evidence_count = 3
        self.repo.upsert_hypothesis(hyp)

        observing = self.repo.list_hypotheses("user-1", states=[HypothesisState.OBSERVING])
        self.assertEqual(len(observing), 1)
        self.assertEqual(observing[0].evidence_count, 3)
        self.assertEqual(observing[0].state, HypothesisState.OBSERVING)

    def test_delete_memory_records_by_id(self) -> None:
        self.repo.create_episode(self._episode("e1", NOW, "meal"))
        self.repo.upsert_profile_item(
            L2ProfileItem(
                item_id="p1",
                user_id="user-1",
                key="sleep",
                value={"late": True},
            )
        )
        self.repo.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id="h1",
                user_id="user-1",
                statement="Late dinner runs high",
                state=HypothesisState.ARCHIVED,
            )
        )

        self.assertTrue(self.repo.delete_episode("e1"))
        self.assertTrue(self.repo.delete_profile_item("p1"))
        self.assertTrue(self.repo.delete_hypothesis("h1"))
        self.assertIsNone(self.repo.get_episode("e1"))
        self.assertEqual(self.repo.list_profile_items("user-1", active_only=False), [])
        self.assertEqual(self.repo.list_hypotheses("user-1", states=[HypothesisState.ARCHIVED]), [])

    def test_candidate_queue_enqueue_and_resolve(self) -> None:
        cand = MemoryCandidate(
            candidate_id="c1",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Confirmed lunch spike",
            requires_user_confirmation=True,
            confidence=0.7,
        )
        self.repo.enqueue_candidate(cand)

        pending = self.repo.list_candidates("user-1", status=CandidateStatus.PENDING)
        self.assertEqual(len(pending), 1)

        resolved = self.repo.set_candidate_status("c1", status=CandidateStatus.ACCEPTED, when=NOW)
        self.assertEqual(resolved.status, CandidateStatus.ACCEPTED)
        self.assertIsNotNone(resolved.resolved_at)
        self.assertEqual(self.repo.list_candidates("user-1", status=CandidateStatus.PENDING), [])

    def _episode(self, episode_id: str, occurred_at: datetime, episode_type: str) -> L1Episode:
        return L1Episode(
            episode_id=episode_id,
            user_id="user-1",
            occurred_at=occurred_at,
            episode_type=episode_type,
            summary=f"{episode_type} episode",
            evidence_refs=[EvidenceRef(kind="event", ref_id=f"ev-{episode_id}")],
            confidence=0.7,
            created_at=occurred_at,
            last_referenced_at=occurred_at,
        )

    def assertAlmostEqualConfidence(self, a: float, b: float) -> None:
        self.assertTrue(abs(a - b) < 1e-6, f"{a} != {b}")


if __name__ == "__main__":
    unittest.main()
