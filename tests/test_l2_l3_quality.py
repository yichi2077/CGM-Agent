"""L2/L3 memory generation quality tests.

Systematic quality-dimension tests covering gaps identified in the codebase
audit:

- L2 confidence formula precision (0.7/0.9/0.95 cap)
- L2 active state, last_verified, valid_from/valid_to
- L2 decay behavior (single, multi, boundary, restore)
- L3 state machine transition process (not just terminal states)
- L3 statement exact format (including underscore-to-space)
- L3 bitemporal time travel (supersede + as_of query)
- L3 contradiction repository persistence
- L2/L3 guard invariants (confidence range, state legality, evidence consistency)
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    EvidenceRef,
    HypothesisState,
    L1Episode,
    L2ProfileItem,
    L3Hypothesis,
)
from hermes_cgm_agent.services.memory import (
    ConsolidationService,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _make_episode(
    user_id: str,
    episode_type: str,
    occurred_at: datetime,
) -> L1Episode:
    """Helper: create and return a minimal L1Episode."""
    return L1Episode(
        episode_id=new_id(),
        user_id=user_id,
        occurred_at=occurred_at,
        episode_type=episode_type,
        summary=f"{episode_type} at {occurred_at.isoformat()}",
        evidence_refs=[EvidenceRef(kind="event", ref_id=f"ev-{occurred_at.date()}")],
        confidence=0.7,
        created_at=occurred_at,
        last_referenced_at=occurred_at,
    )


class _BaseTest(unittest.TestCase):
    """Shared setup: temp SQLite store + repository + consolidation service."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repo = SQLiteMemoryRepository(self.store)
        self.svc = ConsolidationService(repository=self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _episode(self, episode_type: str, occurred_at: datetime) -> L1Episode:
        ep = _make_episode("u1", episode_type, occurred_at)
        return self.repo.create_episode(ep)

    def _episodes_for_days(self, episode_type: str, num_days: int) -> None:
        """Create episodes on num_days distinct days ending at NOW."""
        for d in range(num_days):
            self._episode(episode_type, NOW - timedelta(days=d))


# ─── L2 Confidence Formula ─────────────────────────────────────────────

class L2ConfidenceQualityTests(_BaseTest):
    """L2 confidence = min(0.95, round(0.4 + 0.1 * day_count, 4))."""

    def test_confidence_3_days_equals_0_7(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        items = self.repo.list_profile_items("u1")
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0].confidence, 0.7, places=4)

    def test_confidence_5_days_equals_0_9(self) -> None:
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        items = self.repo.list_profile_items("u1")
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0].confidence, 0.9, places=4)

    def test_confidence_6_days_capped_at_0_95(self) -> None:
        self._episodes_for_days("hyper", 6)
        self.svc.consolidate("u1", now=NOW)
        items = self.repo.list_profile_items("u1")
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0].confidence, 0.95, places=4)

    def test_confidence_10_days_still_0_95(self) -> None:
        self._episodes_for_days("hyper", 10)
        self.svc.consolidate("u1", now=NOW)
        items = self.repo.list_profile_items("u1")
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0].confidence, 0.95, places=4)

    def test_confidence_precision_4_decimals(self) -> None:
        """round(0.4 + 0.1 * 3, 4) = 0.7 — not 0.7000000001."""
        self._episodes_for_days("hypo", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        # Should be exactly 0.7, not a float artifact
        self.assertEqual(item.confidence, round(0.7, 4))


# ─── L2 Active State & Timestamps ──────────────────────────────────────

class L2ActiveStateTests(_BaseTest):
    """L2 is_active, last_verified, valid_from/valid_to after generation."""

    def test_new_belief_is_active(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        self.assertTrue(item.is_active)

    def test_new_belief_last_verified_is_now(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        self.assertEqual(item.last_verified, NOW)

    def test_new_belief_valid_to_is_none(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        self.assertIsNone(item.valid_to)

    def test_new_belief_valid_from_is_set(self) -> None:
        """valid_from defaults to utc_now() — verify it's a recent datetime."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        # valid_from is set by the model default (utc_now), not the consolidate
        # `now` parameter, so we verify it's a plausible recent timestamp.
        from hermes_cgm_agent.domain.cgm import utc_now
        delta = abs((utc_now() - item.valid_from).total_seconds())
        self.assertLess(delta, 10, "valid_from should be close to wall-clock time")

    def test_new_belief_has_source_episode_ids(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        self.assertEqual(len(item.source_episode_ids), 3)

    def test_new_belief_value_has_summary(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        self.assertIn("summary", item.value)
        self.assertIn("偏高片段", item.value["summary"])


# ─── L2 Decay Quality ──────────────────────────────────────────────────

class L2DecayQualityTests(_BaseTest):
    """L2 decay: 30 days stale → -0.2, < 0.3 → deactivate."""

    def test_decay_reduces_confidence_by_0_2(self) -> None:
        self._episodes_for_days("hyper", 3)  # confidence = 0.7
        self.svc.consolidate("u1", now=NOW)

        # 35 days later → stale (>= 30)
        later = NOW + timedelta(days=35)
        self.repo.decay_profile_items(now=later)

        item = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertAlmostEqual(item.confidence, 0.5, places=4)  # 0.7 - 0.2

    def test_decay_multiple_times(self) -> None:
        """0.7 → 0.5 → 0.3 → 0.1 (but 0.3 stays active, 0.1 deactivates)."""
        self._episodes_for_days("hyper", 3)  # confidence = 0.7
        self.svc.consolidate("u1", now=NOW)

        # First decay: 35 days later
        t1 = NOW + timedelta(days=35)
        self.repo.decay_profile_items(now=t1)
        item = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertAlmostEqual(item.confidence, 0.5, places=4)

        # Second decay: 65 days later (30 more days stale)
        t2 = NOW + timedelta(days=65)
        self.repo.decay_profile_items(now=t2)
        item = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertAlmostEqual(item.confidence, 0.3, places=4)
        self.assertTrue(item.is_active)  # 0.3 is NOT below 0.3

        # Third decay: 95 days later
        t3 = NOW + timedelta(days=95)
        self.repo.decay_profile_items(now=t3)
        item = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertAlmostEqual(item.confidence, 0.1, places=4)
        self.assertFalse(item.is_active)  # 0.1 < 0.3 → deactivated

    def test_decay_below_0_3_deactivates(self) -> None:
        # Start with confidence 0.45 (manual upsert)
        item = L2ProfileItem(
            item_id=new_id(),
            user_id="u1",
            key="pattern:hyper",
            value={"summary": "test"},
            confidence=0.45,
            evidence_count=3,
            last_verified=NOW - timedelta(days=35),
            source_episode_ids=["ep1", "ep2", "ep3"],
            created_at=NOW - timedelta(days=35),
            updated_at=NOW - timedelta(days=35),
        )
        self.repo.upsert_profile_item(item)

        self.repo.decay_profile_items(now=NOW)
        loaded = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertAlmostEqual(loaded.confidence, 0.25, places=4)
        self.assertFalse(loaded.is_active)

    def test_decay_exactly_0_3_stays_active(self) -> None:
        """confidence == 0.3 → still active (boundary: deactivate_below is strict <)."""
        item = L2ProfileItem(
            item_id=new_id(),
            user_id="u1",
            key="pattern:hyper",
            value={"summary": "test"},
            confidence=0.5,
            evidence_count=3,
            last_verified=NOW - timedelta(days=35),
            source_episode_ids=["ep1", "ep2", "ep3"],
            created_at=NOW - timedelta(days=35),
            updated_at=NOW - timedelta(days=35),
        )
        self.repo.upsert_profile_item(item)

        # First decay: 0.5 → 0.3
        self.repo.decay_profile_items(now=NOW)
        loaded = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertAlmostEqual(loaded.confidence, 0.3, places=4)
        # 0.3 is NOT < 0.3, so should still be active
        self.assertTrue(loaded.is_active)

    def test_reverify_restores_active(self) -> None:
        """A deactivated L2 that gets re-upserted (new evidence) → is_active=True."""
        # Create initial belief, then decay it to deactivation
        self._episodes_for_days("hyper", 3)  # confidence = 0.7
        self.svc.consolidate("u1", now=NOW)

        # Decay 3 times to go below 0.3: 0.7 → 0.5 → 0.3 → 0.1
        for days_offset in [35, 65, 95]:
            self.repo.decay_profile_items(now=NOW + timedelta(days=days_offset))
        item = self.repo.list_profile_items("u1", active_only=False)[0]
        self.assertFalse(item.is_active)

        # Now add new episodes and consolidate again (re-verify)
        t_new = NOW + timedelta(days=100)
        for d in range(3):
            self._episode("hyper", t_new + timedelta(days=d))
        self.svc.consolidate("u1", now=t_new + timedelta(days=5))

        reloaded = self.repo.list_profile_items("u1")
        self.assertEqual(len(reloaded), 1)
        self.assertTrue(reloaded[0].is_active)
        self.assertGreater(reloaded[0].confidence, 0.3)


# ─── L3 State Transition Process ───────────────────────────────────────

class L3StateTransitionTests(_BaseTest):
    """L3 state machine: 3d→OBSERVING, 4d→still OBSERVING, 5d→STABLE."""

    def test_3_days_creates_observing(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)
        self.assertEqual(hyps[0].evidence_count, 3)

    def test_4_days_stays_observing(self) -> None:
        """4 days < l3_stable_threshold(5) → still OBSERVING."""
        self._episodes_for_days("hyper", 4)
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)
        self.assertEqual(hyps[0].evidence_count, 4)

    def test_5_days_upgrades_to_stable(self) -> None:
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(len(hyps), 1)
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)
        self.assertEqual(hyps[0].evidence_count, 5)

    def test_incremental_upgrade_3_to_5(self) -> None:
        """First 3 days → OBSERVING, then add 2 more days → STABLE."""
        # Day 1-3: create 3 episodes and consolidate
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)

        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.OBSERVING)
        self.assertEqual(hyps[0].evidence_count, 3)

        # Add 2 more days of episodes (days 4 and 5)
        for d in range(2):
            self._episode("hyper", NOW + timedelta(days=d + 1))

        self.svc.consolidate("u1", now=NOW + timedelta(days=3))

        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)
        self.assertEqual(hyps[0].evidence_count, 5)

    def test_contradiction_2_does_not_downgrade(self) -> None:
        """Exactly 2 opposite episodes (< threshold 3) → no downgrade."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)

        # Add only 2 contradicting hypo episodes (< threshold 3)
        for d in range(2):
            self._episode("hypo", NOW + timedelta(days=d + 1))
        self.svc.consolidate("u1", now=NOW + timedelta(days=5))

        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)
        self.assertEqual(hyps[0].contra_count, 0)

    def test_contradiction_3_downgrades_stable_to_observing(self) -> None:
        """3 opposite episodes (>= threshold) → STABLE → OBSERVING."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        hyps = self.repo.list_hypotheses("u1")
        self.assertEqual(hyps[0].state, HypothesisState.STABLE)

        for d in range(3):
            self._episode("hypo", NOW + timedelta(days=d + 1))
        self.svc.consolidate("u1", now=NOW + timedelta(days=10))

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        hyper_hyp = next(h for h in all_hyps if "hyper" in h.statement)
        self.assertEqual(hyper_hyp.state, HypothesisState.OBSERVING)
        self.assertEqual(hyper_hyp.contra_count, 1)

    def test_contra_count_2_intermediate_state(self) -> None:
        """2 consecutive contradicting runs (< limit 3) → still OBSERVING, contra_count=2."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)

        # Run 1: 3 hypo episodes
        for d in range(3):
            self._episode("hypo", NOW + timedelta(days=d + 1))
        self.svc.consolidate("u1", now=NOW + timedelta(days=5))

        # Run 2: 3 more NEW hypo episodes
        for d in range(3):
            self._episode("hypo", NOW + timedelta(days=d + 10))
        self.svc.consolidate("u1", now=NOW + timedelta(days=15))

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        hyper_hyp = next(h for h in all_hyps if "hyper" in h.statement)
        self.assertEqual(hyper_hyp.contra_count, 2)
        self.assertEqual(hyper_hyp.state, HypothesisState.OBSERVING)


# ─── L3 Statement Format ───────────────────────────────────────────────

class L3StatementFormatTests(_BaseTest):
    """L3 statement = 'Recurring {type.replace('_', ' ')} pattern'."""

    def test_statement_format_hyper(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(hyp.statement, "Recurring hyper pattern")

    def test_statement_format_hypo(self) -> None:
        self._episodes_for_days("hypo", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(hyp.statement, "Recurring hypo pattern")

    def test_statement_format_rapid_rise(self) -> None:
        self._episodes_for_days("rapid_rise", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(hyp.statement, "Recurring rapid rise pattern")

    def test_statement_format_rapid_fall(self) -> None:
        self._episodes_for_days("rapid_fall", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(hyp.statement, "Recurring rapid fall pattern")


# ─── L3 Bitemporal Time Travel ─────────────────────────────────────────

class L3BitemporalTests(_BaseTest):
    """L3 bi-temporal: supersede closes old valid_to, time-travel query."""

    def test_new_hypothesis_valid_from_is_set(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertIsNotNone(hyp.valid_from)

    def test_new_hypothesis_valid_to_is_none(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertIsNone(hyp.valid_to)

    def test_supersede_closes_old_valid_to(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        old_hyp = self.repo.list_hypotheses("u1")[0]

        switch = NOW + timedelta(days=30)
        new_hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.STABLE,
            evidence_count=5,
            source_episode_ids=["ep-new"],
            last_checked=switch,
            last_evidence_added=switch,
            created_at=switch,
            updated_at=switch,
        )
        self.repo.supersede_hypothesis(old_hyp.hypothesis_id, new_hyp, now=switch)

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        old = next(h for h in all_hyps if h.hypothesis_id == old_hyp.hypothesis_id)
        new = next(h for h in all_hyps if h.hypothesis_id == new_hyp.hypothesis_id)
        self.assertIsNotNone(old.valid_to)
        self.assertEqual(old.state, HypothesisState.ARCHIVED)
        self.assertIsNone(new.valid_to)

    def test_supersede_links_lineage(self) -> None:
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        old_hyp = self.repo.list_hypotheses("u1")[0]

        switch = NOW + timedelta(days=30)
        new_hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.STABLE,
            evidence_count=5,
            source_episode_ids=["ep-new"],
            last_checked=switch,
            last_evidence_added=switch,
            created_at=switch,
            updated_at=switch,
        )
        self.repo.supersede_hypothesis(old_hyp.hypothesis_id, new_hyp, now=switch)

        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        new = next(h for h in all_hyps if h.hypothesis_id == new_hyp.hypothesis_id)
        self.assertEqual(new.supersedes_hypothesis_id, old_hyp.hypothesis_id)

    def test_time_travel_sees_old_hypothesis(self) -> None:
        """as_of within old hypothesis's valid window → old is visible."""
        from hermes_cgm_agent.domain.cgm import utc_now
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        old_hyp = self.repo.list_hypotheses("u1")[0]

        # Use wall-clock time for switch since valid_from is set by utc_now()
        switch = utc_now() + timedelta(seconds=1)
        new_hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.STABLE,
            evidence_count=5,
            source_episode_ids=["ep-new"],
            last_checked=switch,
            last_evidence_added=switch,
            created_at=switch,
            updated_at=switch,
        )
        self.repo.supersede_hypothesis(old_hyp.hypothesis_id, new_hyp, now=switch)

        # Time travel to mid-window (between old valid_from and switch)
        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        old = next(h for h in all_hyps if h.hypothesis_id == old_hyp.hypothesis_id)
        mid_point = old.valid_from + (old.valid_to - old.valid_from) / 2
        visible = [
            h for h in all_hyps
            if h.valid_from <= mid_point and (h.valid_to is None or h.valid_to > mid_point)
        ]
        ids = {h.hypothesis_id for h in visible}
        self.assertIn(old_hyp.hypothesis_id, ids)
        self.assertNotIn(new_hyp.hypothesis_id, ids)

    def test_time_travel_does_not_see_new(self) -> None:
        """as_of before new hypothesis's valid_from → new is NOT visible."""
        from hermes_cgm_agent.domain.cgm import utc_now
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        old_hyp = self.repo.list_hypotheses("u1")[0]

        switch = utc_now() + timedelta(seconds=1)
        new_hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.STABLE,
            evidence_count=5,
            source_episode_ids=["ep-new"],
            last_checked=switch,
            last_evidence_added=switch,
            created_at=switch,
            updated_at=switch,
        )
        self.repo.supersede_hypothesis(old_hyp.hypothesis_id, new_hyp, now=switch)

        # Use mid-point of old's valid window (before switch)
        all_hyps = self.repo.list_hypotheses("u1", active_only=False)
        old = next(h for h in all_hyps if h.hypothesis_id == old_hyp.hypothesis_id)
        mid_point = old.valid_from + (old.valid_to - old.valid_from) / 2
        visible = [
            h for h in all_hyps
            if h.valid_from <= mid_point and (h.valid_to is None or h.valid_to > mid_point)
        ]
        for h in visible:
            self.assertNotEqual(h.hypothesis_id, new_hyp.hypothesis_id)

    def test_active_only_excludes_archived(self) -> None:
        """active_only=True excludes superseded/archived hypotheses."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        old_hyp = self.repo.list_hypotheses("u1")[0]

        switch = NOW + timedelta(days=30)
        new_hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.STABLE,
            evidence_count=5,
            source_episode_ids=["ep-new"],
            last_checked=switch,
            last_evidence_added=switch,
            created_at=switch,
            updated_at=switch,
        )
        self.repo.supersede_hypothesis(old_hyp.hypothesis_id, new_hyp, now=switch)

        active = self.repo.list_hypotheses("u1", active_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].hypothesis_id, new_hyp.hypothesis_id)


# ─── L3 Contradiction Repository Persistence ───────────────────────────

class L3ContradictionRepositoryTests(_BaseTest):
    """L3 contra_count and contra_episode_ids persistence at repository level."""

    def test_contra_count_persists(self) -> None:
        """contra_count written via upsert_hypothesis is read back correctly."""
        hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.OBSERVING,
            evidence_count=3,
            contra_count=2,
            last_checked=NOW,
            last_evidence_added=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        self.repo.upsert_hypothesis(hyp)
        loaded = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(loaded.contra_count, 2)

    def test_contra_episode_ids_persists(self) -> None:
        """contra_episode_ids list survives a write-read roundtrip."""
        hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.OBSERVING,
            evidence_count=3,
            contra_count=1,
            contra_episode_ids=["ep-contra-1", "ep-contra-2", "ep-contra-3"],
            last_checked=NOW,
            last_evidence_added=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        self.repo.upsert_hypothesis(hyp)
        loaded = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(set(loaded.contra_episode_ids), {"ep-contra-1", "ep-contra-2", "ep-contra-3"})

    def test_contra_episode_ids_empty_default(self) -> None:
        """Newly created hypothesis without contradictions has empty contra_episode_ids."""
        hyp = L3Hypothesis(
            hypothesis_id=new_id(),
            user_id="u1",
            statement="Recurring hyper pattern",
            state=HypothesisState.OBSERVING,
            evidence_count=3,
            last_checked=NOW,
            last_evidence_added=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        self.repo.upsert_hypothesis(hyp)
        loaded = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(loaded.contra_episode_ids, [])
        self.assertEqual(loaded.contra_count, 0)


# ─── L2/L3 Guard Invariants ────────────────────────────────────────────

class L2L3GuardTests(_BaseTest):
    """Quality guard invariants verified on consolidation-generated data."""

    def test_l2_confidence_in_valid_range(self) -> None:
        """All L2 confidence values ∈ (0, 0.95]."""
        for episode_type in ["hyper", "hypo", "rapid_rise", "rapid_fall"]:
            self._episodes_for_days(episode_type, 3)
        self.svc.consolidate("u1", now=NOW)

        for item in self.repo.list_profile_items("u1"):
            self.assertGreater(item.confidence, 0)
            self.assertLessEqual(item.confidence, 0.95)

    def test_l3_state_is_valid_enum(self) -> None:
        """All L3 states are valid HypothesisState values."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)

        valid_states = {
            HypothesisState.CANDIDATE,
            HypothesisState.OBSERVING,
            HypothesisState.STABLE,
            HypothesisState.ARCHIVED,
        }
        for hyp in self.repo.list_hypotheses("u1", active_only=False):
            self.assertIn(hyp.state, valid_states)

    def test_l3_contra_count_non_negative(self) -> None:
        """L3 contra_count >= 0 for all hypotheses."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        for hyp in self.repo.list_hypotheses("u1", active_only=False):
            self.assertGreaterEqual(hyp.contra_count, 0)

    def test_l2_evidence_count_matches_source_ids(self) -> None:
        """L2 evidence_count == len(source_episode_ids)."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        item = self.repo.list_profile_items("u1")[0]
        self.assertEqual(item.evidence_count, len(item.source_episode_ids))

    def test_l3_evidence_count_le_source_ids(self) -> None:
        """L3 evidence_count <= len(source_episode_ids)."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertLessEqual(hyp.evidence_count, len(hyp.source_episode_ids))

    def test_l3_stable_has_sufficient_evidence(self) -> None:
        """state == STABLE → evidence_count >= l3_stable_threshold (5)."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(hyp.state, HypothesisState.STABLE)
        self.assertGreaterEqual(hyp.evidence_count, 5)

    def test_l3_observing_has_minimum_evidence(self) -> None:
        """state == OBSERVING → evidence_count >= l3_min_pattern (3)."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertEqual(hyp.state, HypothesisState.OBSERVING)
        self.assertGreaterEqual(hyp.evidence_count, 3)

    def test_l2_key_starts_with_pattern(self) -> None:
        """All L2 keys start with 'pattern:'."""
        for ep_type in ["hyper", "hypo"]:
            self._episodes_for_days(ep_type, 3)
        self.svc.consolidate("u1", now=NOW)
        for item in self.repo.list_profile_items("u1"):
            self.assertTrue(item.key.startswith("pattern:"),
                            f"L2 key doesn't start with 'pattern:': {item.key}")

    def test_l3_statement_starts_with_recurring(self) -> None:
        """All L3 statements start with 'Recurring '."""
        for ep_type in ["hyper", "hypo", "rapid_rise"]:
            self._episodes_for_days(ep_type, 3)
        self.svc.consolidate("u1", now=NOW)
        for hyp in self.repo.list_hypotheses("u1"):
            self.assertTrue(hyp.statement.startswith("Recurring "),
                            f"L3 statement doesn't start with 'Recurring ': {hyp.statement}")

    def test_l2_active_items_have_valid_to_none(self) -> None:
        """Active L2 items (is_active=True) must have valid_to=None."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        for item in self.repo.list_profile_items("u1"):  # active_only=True default
            self.assertTrue(item.is_active)
            self.assertIsNone(item.valid_to)

    def test_l3_active_hypotheses_have_valid_to_none(self) -> None:
        """Active L3 hypotheses (valid_to IS NULL) must not be ARCHIVED."""
        self._episodes_for_days("hyper", 5)
        self.svc.consolidate("u1", now=NOW)
        for hyp in self.repo.list_hypotheses("u1"):  # active_only=True default
            self.assertIsNone(hyp.valid_to)
            self.assertNotEqual(hyp.state, HypothesisState.ARCHIVED)

    def test_l3_last_evidence_added_is_set(self) -> None:
        """Newly created L3 hypothesis has last_evidence_added != None."""
        self._episodes_for_days("hyper", 3)
        self.svc.consolidate("u1", now=NOW)
        hyp = self.repo.list_hypotheses("u1")[0]
        self.assertIsNotNone(hyp.last_evidence_added)

    def test_multiple_episode_types_generate_multiple_beliefs(self) -> None:
        """Different episode types generate separate L2 beliefs and L3 hypotheses."""
        for ep_type in ["hyper", "hypo", "rapid_rise", "rapid_fall"]:
            self._episodes_for_days(ep_type, 3)
        self.svc.consolidate("u1", now=NOW)

        items = self.repo.list_profile_items("u1")
        hyps = self.repo.list_hypotheses("u1")
        keys = {item.key for item in items}
        statements = {hyp.statement for hyp in hyps}

        self.assertEqual(len(items), 4)
        self.assertEqual(len(hyps), 4)
        self.assertEqual(keys, {"pattern:hyper", "pattern:hypo", "pattern:rapid_rise", "pattern:rapid_fall"})
        self.assertEqual(statements, {
            "Recurring hyper pattern",
            "Recurring hypo pattern",
            "Recurring rapid rise pattern",
            "Recurring rapid fall pattern",
        })


if __name__ == "__main__":
    unittest.main()
