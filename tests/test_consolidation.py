from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from hermes_cgm_agent.domain import (
    EvidenceRef,
    HypothesisState,
    L1Episode,
    L3Hypothesis,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.services.audit import AuditService
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
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repo = SQLiteMemoryRepository(self.store)
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
        self.assertEqual(
            set(beliefs[0].source_episode_ids),
            {episode.episode_id for episode in self.repo.list_episodes("u1")},
        )
        # B1: the belief carries a human-readable summary (renders as a sentence
        # in USER.md, not bare JSON).
        self.assertIn("summary", beliefs[0].value)
        self.assertIn("偏高片段", beliefs[0].value["summary"])  # D053 life-language
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)
        self.assertEqual(
            set(hyps[0].source_episode_ids),
            {episode.episode_id for episode in self.repo.list_episodes("u1")},
        )

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

    def test_consolidate_writes_audit_when_configured(self) -> None:
        self._episode("hyper", NOW)
        svc = ConsolidationService(
            repository=self.repo,
            audit_service=AuditService(self.store),
        )

        svc.consolidate("u1", now=NOW, session_id="session-1")

        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM audit_logs
                WHERE event_type = 'memory_consolidation'
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        payload = self.store.unseal(row["payload_json"], legacy="json")
        self.assertEqual(payload["user_id"], "u1")
        self.assertEqual(payload["status"], "ok")

    def test_consolidate_archives_stale_l1(self) -> None:
        old = self._episode("note", NOW - timedelta(days=200))
        old.last_referenced_at = NOW - timedelta(days=120)
        # rewrite with stale ref time
        self.repo.touch_episode(old.episode_id, when=NOW - timedelta(days=120))

        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.episodes_archived, 1)
        self.assertEqual(self.repo.list_episodes("u1"), [])

    def test_stale_l1_does_not_promote_before_archive(self) -> None:
        for d in range(3):
            self._episode("hyper", NOW - timedelta(days=100 + d))
        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.episodes_archived, 3)
        self.assertEqual(report.profiles_updated, 0)
        self.assertEqual(report.hypotheses_updated, 0)
        self.assertEqual(self.repo.list_profile_items("u1"), [])
        self.assertEqual(self.repo.list_hypotheses("u1"), [])

    # -- B1: contradiction detection + forgetting ----------------------------

    def test_contradiction_downgrades_stable_to_observing(self) -> None:
        """B1: STABLE hypothesis + contradiction evidence >= N -> OBSERVING."""
        # 5 distinct days of hyper -> STABLE hypothesis
        for d in range(5):
            self._episode("hyper", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)

        # Add 3 NEW distinct days of hypo (after last_checked=NOW) ->
        # contradictions for the hyper hypothesis.
        for d in range(3):
            self._episode("hypo", NOW + timedelta(days=d + 1))

        self.svc.consolidate("u1", now=NOW + timedelta(days=10))

        # The hyper hypothesis should be downgraded from STABLE to OBSERVING
        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        hyper_hyp = next(h for h in all_hyps if h.statement == "Recurring hyper pattern")
        self.assertEqual(hyper_hyp.state, HypothesisState.OBSERVING)
        self.assertEqual(hyper_hyp.contra_count, 1)

    def test_consecutive_contradiction_archives_hypothesis(self) -> None:
        """B1: consecutive M contradictions -> ARCHIVED (valid_to closed)."""
        # 5 distinct days of hyper -> STABLE
        for d in range(5):
            self._episode("hyper", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)

        # Run consolidate 3 times (l3_consecutive_contra_limit = 3), each time
        # adding NEW hypo episodes that occurred after the previous
        # last_checked timestamp (idempotency: old episodes are not re-counted).
        for i in range(3):
            base = NOW + timedelta(days=i * 10 + 1)
            for d in range(3):
                self._episode("hypo", base + timedelta(days=d))
            self.svc.consolidate("u1", now=base + timedelta(days=5))

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        hyper_hyp = next(h for h in all_hyps if h.statement == "Recurring hyper pattern")
        self.assertEqual(hyper_hyp.state, HypothesisState.ARCHIVED)
        self.assertIsNotNone(hyper_hyp.valid_to)
        # active_only=True should now exclude the archived hypothesis
        active = [h for h in self.repo.list_hypotheses("u1") if h.statement == "Recurring hyper pattern"]
        self.assertEqual(active, [])

    def test_no_contradiction_resets_contra_count(self) -> None:
        """B1: a clean run (no contradictions) resets the consecutive counter."""
        for d in range(5):
            self._episode("hyper", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)

        # Add 3 NEW hypo (after last_checked=NOW) -> one contradicting run
        hypo_eps = []
        for d in range(3):
            ep = self._episode("hypo", NOW + timedelta(days=d + 1))
            hypo_eps.append(ep)
        self.svc.consolidate("u1", now=NOW + timedelta(days=10))

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        hyper_hyp = next(h for h in all_hyps if h.statement == "Recurring hyper pattern")
        self.assertEqual(hyper_hyp.contra_count, 1)

        # Delete the hypo episodes so the next run sees no new contradictions
        # -> no contradiction -> counter reset to 0.
        for ep in hypo_eps:
            self.repo.delete_episode(ep.episode_id)

        self.svc.consolidate("u1", now=NOW + timedelta(days=20))

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        hyper_hyp = next(h for h in all_hyps if h.statement == "Recurring hyper pattern")
        self.assertEqual(hyper_hyp.contra_count, 0)

    def test_delayed_opposite_episodes_are_counted_once(self) -> None:
        for d in range(5):
            self._episode("hyper", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)
        # Source-poll preserves a historical clinical time but creates the L1
        # record now. The durable ID ledger must still count it once.
        delayed = [
            L1Episode(
                episode_id=f"delayed-hypo-{index}",
                user_id="u1",
                occurred_at=NOW - timedelta(hours=index + 1),
                episode_type="hypo",
                summary="late arriving hypo",
                created_at=NOW + timedelta(minutes=1),
                last_referenced_at=NOW + timedelta(minutes=1),
            )
            for index in range(3)
        ]
        for episode in delayed:
            self.repo.create_episode(episode)
        self.svc.consolidate("u1", now=NOW + timedelta(minutes=1))
        hyper = next(
            h for h in self.repo.list_hypotheses("u1", active_only=False)
            if h.statement == "Recurring hyper pattern"
        )
        self.assertEqual(hyper.state, HypothesisState.OBSERVING)
        self.assertEqual(hyper.contra_count, 1)
        self.assertEqual(set(hyper.contra_episode_ids), {episode.episode_id for episode in delayed})

        self.svc.consolidate("u1", now=NOW + timedelta(minutes=2))
        hyper = next(
            h for h in self.repo.list_hypotheses("u1", active_only=False)
            if h.statement == "Recurring hyper pattern"
        )
        self.assertEqual(hyper.contra_count, 0)

    def test_visible_opposite_episodes_do_not_become_new_contradictions(self) -> None:
        """A newly formed hypothesis must not contradict itself on rerun."""
        for d in range(3):
            self._episode("hyper", NOW - timedelta(days=d))
            self._episode("hypo", NOW - timedelta(days=10 + d))

        self.svc.consolidate("u1", now=NOW)
        self.svc.consolidate("u1", now=NOW + timedelta(minutes=1))

        hyper = next(
            h for h in self.repo.list_hypotheses("u1", active_only=False)
            if h.statement == "Recurring hyper pattern"
        )
        self.assertEqual(hyper.contra_count, 0)
        self.assertEqual(len(hyper.contra_episode_ids), 3)

    def test_decay_hypotheses_downgrades_stable_after_decay_idle(self) -> None:
        """B1: STABLE hypothesis idle >= 90 days -> downgraded to OBSERVING."""
        old_checked = NOW - timedelta(days=100)
        self.repo.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=new_id(),
                user_id="u1",
                statement="Recurring some_pattern pattern",
                state=HypothesisState.STABLE,
                evidence_count=5,
                last_checked=old_checked,
                created_at=old_checked,
                updated_at=old_checked,
            )
        )
        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.hypotheses_decayed, 1)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)

    def test_decay_hypotheses_archives_after_archive_idle(self) -> None:
        """B1: hypothesis idle >= 180 days -> valid_to closed (ARCHIVED)."""
        old_checked = NOW - timedelta(days=200)
        self.repo.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=new_id(),
                user_id="u1",
                statement="Recurring some_pattern pattern",
                state=HypothesisState.STABLE,
                evidence_count=5,
                last_checked=old_checked,
                created_at=old_checked,
                updated_at=old_checked,
            )
        )
        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.hypotheses_decayed, 1)
        # Archived hypothesis has valid_to set -> filtered by active_only=True
        self.assertEqual(self.repo.list_hypotheses("u1"), [])
        archived = self.repo.list_hypotheses("u1", active_only=False)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].state, HypothesisState.ARCHIVED)
        self.assertIsNotNone(archived[0].valid_to)

    def test_decay_triggers_even_when_advance_updates_last_checked(self) -> None:
        """C-02: decay must use last_evidence_added, not last_checked.

        Without the fix, _advance_hypothesis updates last_checked=now
        before decay_hypotheses runs, so idle_days is always 0 and decay
        never triggers — even when the hypothesis has been idle for months.

        This test creates a STABLE hypothesis with matching episodes,
        then calls consolidate 91 days later.  The hypothesis must be
        downgraded to OBSERVING despite _advance_hypothesis running.
        """
        # Create 5 days of hyper episodes to form a STABLE hypothesis.
        for d in range(5):
            self._episode("hyper", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)

        # 91 days later, call consolidate again.  The episodes still
        # exist, so _advance_hypothesis will run and update last_checked
        # to now+91d.  But last_evidence_added should remain at the
        # original time (no new episodes were added).
        later = NOW + timedelta(days=91)
        report = self.svc.consolidate("u1", now=later)
        # Decay should have triggered: 91 days >= l3_decay_idle_days (90).
        self.assertEqual(report.hypotheses_decayed, 1)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)

    def test_decay_archives_after_180_days_with_matching_episodes(self) -> None:
        """C-02: 180-day archive also works with matching episodes present."""
        for d in range(5):
            self._episode("hyper", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)

        later = NOW + timedelta(days=181)
        self.svc.consolidate("u1", now=later)
        # Archived hypothesis has valid_to set -> filtered by active_only=True
        self.assertEqual(self.repo.list_hypotheses("u1"), [])
        archived = self.repo.list_hypotheses("u1", active_only=False)
        self.assertEqual(archived[0].state, HypothesisState.ARCHIVED)

    def test_rapid_rise_contradicted_by_rapid_fall(self) -> None:
        """H-04: rapid_rise hypothesis is contradicted by rapid_fall episodes."""
        for d in range(5):
            self._episode("rapid_rise", NOW - timedelta(days=d))
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)

        # Add 3 contradicting rapid_fall episodes (threshold is 3).
        for i in range(3):
            self._episode("rapid_fall", NOW + timedelta(hours=i + 1))
        report = self.svc.consolidate("u1", now=NOW + timedelta(hours=4))
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)

    def test_substring_no_false_positive(self) -> None:
        """H-04/Codex: 'hyperglycemia' in statement must not match 'hyper' rule."""
        # Insert a hypothesis with a statement containing 'hyperglycemia'
        # but not the pattern 'recurring hyper pattern'.
        self.repo.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=new_id(),
                user_id="u1",
                statement="Recurring hyperglycemia risk pattern",
                state=HypothesisState.STABLE,
                evidence_count=5,
                last_checked=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        # Add hypo episodes — they should NOT be counted as contradictions
        # because the statement doesn't match the 'recurring hyper pattern'.
        self._episode("hypo", NOW)
        report = self.svc.consolidate("u1", now=NOW + timedelta(hours=1))
        hyps = self.repo.list_hypotheses("u1")
        # Hypothesis should remain STABLE (no contradiction detected).
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)

    # -- B3: transaction safety ----------------------------------------------

    def test_transaction_rollback_on_error(self) -> None:
        """B3: transaction() rolls back all writes on error."""
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.create_audit_log(
                    session_id="test-rollback",
                    event_type="test_rollback",
                    payload={"key": "value"},
                )
                raise RuntimeError("simulated crash")

        # The audit log entry should not have been committed
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM audit_logs WHERE event_type = 'test_rollback'"
            ).fetchone()
        self.assertEqual(row["cnt"], 0)

    def test_transaction_commits_on_success(self) -> None:
        """B3: transaction() commits all writes on normal exit."""
        with self.store.transaction():
            self.store.create_audit_log(
                session_id="test-commit",
                event_type="test_commit",
                payload={"key": "value"},
            )

        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM audit_logs WHERE event_type = 'test_commit'"
            ).fetchone()
        self.assertEqual(row["cnt"], 1)

    def test_nested_transaction_rolls_back_caught_inner_failure(self) -> None:
        with self.store.transaction():
            self.store.create_audit_log(
                session_id="outer", event_type="outer_before", payload={}
            )
            try:
                with self.store.transaction():
                    self.store.create_audit_log(
                        session_id="inner", event_type="inner_failed", payload={}
                    )
                    raise RuntimeError("inner failure")
            except RuntimeError:
                pass
            self.store.create_audit_log(
                session_id="outer", event_type="outer_after", payload={}
            )
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_logs "
                "WHERE event_type IN ('outer_before', 'inner_failed', 'outer_after')"
            ).fetchall()
        self.assertEqual({row["event_type"] for row in rows}, {"outer_before", "outer_after"})

    def test_transaction_connection_is_not_shared_across_threads(self) -> None:
        errors: list[Exception] = []

        def read_from_other_thread() -> None:
            try:
                with self.store.connect() as conn:
                    conn.execute("SELECT 1").fetchone()
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        with self.store.transaction():
            thread = threading.Thread(target=read_from_other_thread)
            thread.start()
            thread.join()
        self.assertEqual(errors, [])

    # -- B4: candidate TTL + purge -------------------------------------------

    def test_consolidate_purges_expired_candidates(self) -> None:
        """B4: consolidate() purges expired pending candidates at the start."""
        old_cand = MemoryCandidate(
            candidate_id="c-old",
            user_id="u1",
            target_layer=MemoryLayer.L1,
            candidate_type="episode",
            summary="Old candidate",
            confidence=0.7,
            created_at=NOW - timedelta(days=40),
        )
        self.repo.enqueue_candidate(old_cand)

        report = self.svc.consolidate("u1", now=NOW)
        self.assertEqual(report.candidates_purged, 1)
        self.assertEqual(self.repo.list_candidates("u1"), [])

    def test_default_consolidation_timezone_uses_environment(self) -> None:
        timestamps = [
            datetime(2026, 6, 2, 7, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 23, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc),
        ]
        for timestamp in timestamps:
            self._episode("hyper", timestamp)
        with mock.patch.dict("os.environ", {"CGM_AGENT_TIMEZONE": "America/Los_Angeles"}):
            service = ConsolidationService(repository=self.repo)
            report = service.consolidate("u1", now=datetime(2026, 6, 4, tzinfo=timezone.utc))
        self.assertEqual(report.profiles_updated, 0)

    # -- B5: summary purge ---------------------------------------------------

    def test_synthesize_state_purges_old_summaries(self) -> None:
        """B5: synthesize_state purges old summaries, keeping only keep_count."""
        config = ConsolidationConfig(summary_keep_count=5)
        svc = ConsolidationService(repository=self.repo, config=config)

        for i in range(10):
            svc.synthesize_state(
                "u1",
                window_start=NOW + timedelta(minutes=i),
                window_end=NOW + timedelta(minutes=i + 1),
                period="daily",
                metrics_summary={"tir_pct": 70 + i},
                now=NOW + timedelta(minutes=i),
            )

        summaries = self.repo.list_summaries("u1")
        self.assertEqual(len(summaries), 5)
        # Most recent should be kept (highest tir_pct since created_at is
        # ascending with i)
        self.assertEqual(summaries[0].metrics["tir_pct"], 79)

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
