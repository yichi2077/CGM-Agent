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
    MemorySummary,
)
from hermes_cgm_agent.services.memory import MemoryReviewService, SQLiteMemoryRepository, new_id
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

    # -- B2: L3 bi-temporal model --------------------------------------------

    def test_supersede_hypothesis_closes_valid_to_and_activates_replacement(self) -> None:
        """B2: supersede_hypothesis closes old valid_to and creates replacement."""
        t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
        old = L3Hypothesis(
            hypothesis_id="h-old",
            user_id="user-1",
            statement="Recurring hyper pattern",
            state=HypothesisState.STABLE,
            evidence_count=5,
            valid_from=t0,
            last_checked=t0,
            created_at=t0,
            updated_at=t0,
        )
        self.repo.upsert_hypothesis(old)

        switch = datetime(2026, 6, 1, tzinfo=timezone.utc)
        new = L3Hypothesis(
            hypothesis_id="h-new",
            user_id="user-1",
            statement="Recurring hypo pattern",
            state=HypothesisState.OBSERVING,
            evidence_count=3,
            last_checked=switch,
            created_at=switch,
            updated_at=switch,
        )
        self.repo.supersede_hypothesis("h-old", new, now=switch)

        all_hyps = self.repo.list_hypotheses("user-1", active_only=False)
        by_id = {h.hypothesis_id: h for h in all_hyps}
        # Old hypothesis: valid_to closed, state ARCHIVED
        self.assertEqual(by_id["h-old"].valid_to, switch)
        self.assertEqual(by_id["h-old"].state, HypothesisState.ARCHIVED)
        # New hypothesis: valid_from set, active
        self.assertEqual(by_id["h-new"].valid_from, switch)
        self.assertIsNone(by_id["h-new"].valid_to)
        self.assertEqual(by_id["h-new"].supersedes_hypothesis_id, "h-old")
        # active_only=True should only return the new one
        active = self.repo.list_hypotheses("user-1")
        self.assertEqual([h.hypothesis_id for h in active], ["h-new"])

    def test_supersede_rejects_same_replacement_identity(self) -> None:
        hyp = L3Hypothesis(
            hypothesis_id="h1",
            user_id="user-1",
            statement="Recurring hyper pattern",
            last_checked=NOW,
        )
        self.repo.upsert_hypothesis(hyp)
        with self.assertRaises(ValueError):
            self.repo.supersede_hypothesis("h1", hyp, now=NOW)
        stored = self.repo.list_hypotheses("user-1", active_only=False)
        self.assertEqual(len(stored), 1)
        self.assertIsNone(stored[0].valid_to)

    def test_list_hypotheses_active_only_excludes_archived(self) -> None:
        """B2: active_only=True filters out hypotheses with valid_to set."""
        active_hyp = L3Hypothesis(
            hypothesis_id="h-active",
            user_id="user-1",
            statement="Active pattern",
            state=HypothesisState.OBSERVING,
            evidence_count=3,
            last_checked=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        archived_hyp = L3Hypothesis(
            hypothesis_id="h-archived",
            user_id="user-1",
            statement="Old pattern",
            state=HypothesisState.ARCHIVED,
            evidence_count=5,
            valid_to=NOW - timedelta(days=1),
            last_checked=NOW - timedelta(days=2),
            created_at=NOW - timedelta(days=10),
            updated_at=NOW - timedelta(days=1),
        )
        self.repo.upsert_hypothesis(active_hyp)
        self.repo.upsert_hypothesis(archived_hyp)

        active = self.repo.list_hypotheses("user-1")
        self.assertEqual([h.hypothesis_id for h in active], ["h-active"])

        all_hyps = self.repo.list_hypotheses("user-1", active_only=False)
        self.assertEqual(len(all_hyps), 2)

    # -- B4: candidate queue TTL + purge -------------------------------------

    def test_enqueue_candidate_sets_expires_at(self) -> None:
        """B4: enqueued candidates get a 30-day TTL (expires_at)."""
        cand = MemoryCandidate(
            candidate_id="c-ttl",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="TTL test",
            confidence=0.7,
            created_at=NOW,
        )
        self.repo.enqueue_candidate(cand)

        with self.repo.store.connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM memory_candidates WHERE candidate_id = 'c-ttl'"
            ).fetchone()
        self.assertIsNotNone(row["expires_at"])
        expires = datetime.fromisoformat(row["expires_at"])
        expected = NOW + timedelta(days=30)
        self.assertEqual(expires, expected)

    def test_purge_expired_candidates_deletes_only_pending(self) -> None:
        """B4: purge_expired_candidates removes only expired pending entries."""
        # Pending + expired (created 40 days ago -> expires_at = 10 days ago)
        old_pending = MemoryCandidate(
            candidate_id="c-old-pending",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Old pending",
            confidence=0.7,
            created_at=NOW - timedelta(days=40),
        )
        self.repo.enqueue_candidate(old_pending)

        # Accepted + expired (should NOT be purged — resolved history)
        old_accepted = MemoryCandidate(
            candidate_id="c-old-accepted",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Old accepted",
            confidence=0.7,
            created_at=NOW - timedelta(days=40),
        )
        self.repo.enqueue_candidate(old_accepted)
        self.repo.set_candidate_status(
            "c-old-accepted", status=CandidateStatus.ACCEPTED, when=NOW - timedelta(days=39)
        )

        # Pending + fresh (should NOT be purged)
        fresh = MemoryCandidate(
            candidate_id="c-fresh",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Fresh",
            confidence=0.7,
            created_at=NOW,
        )
        self.repo.enqueue_candidate(fresh)

        purged = self.repo.purge_expired_candidates(now=NOW)
        self.assertEqual(purged, 1)

        remaining_ids = {c.candidate_id for c in self.repo.list_candidates("user-1")}
        self.assertEqual(remaining_ids, {"c-old-accepted", "c-fresh"})

    def test_initialize_backfills_legacy_candidate_expiry(self) -> None:
        legacy = MemoryCandidate(
            candidate_id="legacy-expiry",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Legacy candidate",
            created_at=NOW - timedelta(days=40),
        )
        self.repo.enqueue_candidate(legacy)
        with self.repo.store.connect() as conn:
            conn.execute(
                "UPDATE memory_candidates SET expires_at = NULL WHERE candidate_id = ?",
                (legacy.candidate_id,),
            )
        self.repo.store.initialize()
        with self.repo.store.connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM memory_candidates WHERE candidate_id = ?",
                (legacy.candidate_id,),
            ).fetchone()
        self.assertEqual(
            datetime.fromisoformat(row["expires_at"]),
            legacy.created_at + timedelta(days=30),
        )
        self.assertEqual(self.repo.purge_expired_candidates(now=NOW), 1)

    def test_expired_candidate_cannot_be_confirmed(self) -> None:
        candidate = MemoryCandidate(
            candidate_id="expired-confirm",
            user_id="user-1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Expired candidate",
            created_at=NOW - timedelta(days=31),
        )
        self.repo.enqueue_candidate(candidate)
        review = MemoryReviewService(repository=self.repo)
        with self.assertRaises(KeyError):
            review.confirm_candidate(
                candidate.candidate_id,
                user_id="user-1",
                confirmed=True,
                now=NOW,
            )
        self.assertEqual(self.repo.list_episodes("user-1"), [])

    # -- B5: summary purge ---------------------------------------------------

    def test_purge_old_summaries_keeps_most_recent(self) -> None:
        """B5: purge_old_summaries retains only the N most recent summaries."""
        for i in range(35):
            self.repo.create_summary(
                MemorySummary(
                    summary_id=f"s-{i}",
                    user_id="user-1",
                    period="daily",
                    window_start=NOW - timedelta(days=35 - i),
                    window_end=NOW - timedelta(days=34 - i),
                    content=f"Summary {i}",
                    metrics={},
                    created_at=NOW - timedelta(days=35 - i),
                )
            )

        purged = self.repo.purge_old_summaries("user-1", keep_count=30)
        self.assertEqual(purged, 5)

        remaining = self.repo.list_summaries("user-1")
        self.assertEqual(len(remaining), 30)
        # Most recent (s-34) should be first (list_summaries orders DESC)
        self.assertEqual(remaining[0].summary_id, "s-34")

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
