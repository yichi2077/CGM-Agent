from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    CandidateStatus,
    G8MemoryCandidate,
    HypothesisState,
    L1Episode,
    L2ProfileItem,
    L3Hypothesis,
    MemoryCandidate,
    MemoryLayer,
)
from hermes_cgm_agent.services.memory import MemoryToolService, SQLiteMemoryRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class MemoryToolServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteMemoryRepository(self.store)
        self.service = MemoryToolService(self.repository)
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        self.repository.create_episode(
            L1Episode(
                episode_id="ep-1",
                user_id="user-1",
                occurred_at=now,
                episode_type="meal",
                summary="Lunch spike",
            )
        )
        self.repository.upsert_profile_item(
            L2ProfileItem(
                item_id="pi-1",
                user_id="user-1",
                key="food:noodle",
                value={"pattern": "post_meal_rise"},
            )
        )
        self.repository.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id="hyp-1",
                user_id="user-1",
                statement="Noodles often run high",
                state=HypothesisState.ARCHIVED,
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_records_returns_requested_layers_and_candidates(self) -> None:
        self.repository.enqueue_candidate(
            MemoryCandidate(
                candidate_id="cand-1",
                user_id="user-1",
                target_layer=MemoryLayer.L1,
                candidate_type="episode",
                summary="Candidate",
            )
        )

        result = self.service.list_records(
            user_id="user-1",
            layer="all",
            include_archived=True,
            candidate_status=CandidateStatus.PENDING,
            limit=None,
        )

        self.assertEqual(result.total_count, 3)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual({item["layer"] for item in result.memories}, {"L1", "L2", "L3"})
        self.assertEqual(result.candidates[0]["candidate_id"], "cand-1")

    def test_list_records_candidates_layer_omits_durable_memories(self) -> None:
        result = self.service.list_records(
            user_id="user-1",
            layer="candidates",
            include_archived=True,
            candidate_status=CandidateStatus.PENDING,
            limit=None,
        )

        self.assertEqual(result.memories, [])
        self.assertEqual(result.total_count, 0)

    def test_delete_record_is_user_scoped(self) -> None:
        self.assertFalse(
            self.service.delete_record(user_id="other-user", memory_id="ep-1", layer="L1")
        )
        self.assertIsNotNone(self.repository.get_episode("ep-1"))

    def test_delete_record_removes_l3(self) -> None:
        deleted = self.service.delete_record(
            user_id="user-1",
            memory_id="hyp-1",
            layer="L3",
        )

        self.assertTrue(deleted)
        self.assertEqual(
            self.repository.list_hypotheses("user-1", states=[HypothesisState.ARCHIVED]),
            [],
        )

    def test_confirm_candidate_promotes_pending_candidate(self) -> None:
        before_count = len(self.repository.list_episodes("user-1"))
        self.repository.enqueue_candidate(
            MemoryCandidate(
                candidate_id="cand-1",
                user_id="user-1",
                target_layer=MemoryLayer.L1,
                candidate_type="episode",
                summary="Candidate",
            )
        )

        status = self.service.confirm_candidate(
            user_id="user-1",
            candidate_id="cand-1",
            confirmed=True,
        )

        self.assertEqual(status, CandidateStatus.ACCEPTED.value)
        self.assertEqual(len(self.repository.list_episodes("user-1")), before_count + 1)

    def test_ingest_report_candidates_disabled_does_not_enqueue(self) -> None:
        result = self.service.ingest_report_candidates(
            report=_ReportFixture(
                report_id="r1",
                user_id="user-1",
                g8_memory_candidates=[
                    G8MemoryCandidate(
                        target_layer="L1",
                        candidate_type="episode",
                        summary="Candidate from report.",
                        confidence=0.7,
                    )
                ],
            ),
            enabled=False,
        )

        self.assertEqual(result["enabled"], False)
        self.assertEqual(result["enqueued"], 0)
        self.assertEqual(self.repository.list_candidates("user-1"), [])

    def test_ingest_report_candidates_enqueues_report_candidates(self) -> None:
        window_start = datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)
        result = self.service.ingest_report_candidates(
            report=_ReportFixture(
                report_id="r1",
                user_id="user-1",
                data_scope=_ScopeFixture(window_start=window_start),
                g8_memory_candidates=[
                    G8MemoryCandidate(
                        target_layer="L1",
                        candidate_type="episode",
                        summary="Candidate from report.",
                        confidence=0.7,
                    )
                ],
            ),
            enabled=True,
        )

        candidates = self.repository.list_candidates("user-1")
        self.assertEqual(result["enabled"], True)
        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(candidates[0].candidate_id, "report-r1-1")
        self.assertEqual(candidates[0].source_report_id, "r1")
        self.assertEqual(candidates[0].occurred_at, window_start)

    def test_report_candidate_explicit_occurred_at_overrides_window_start(self) -> None:
        window_start = datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)
        event_time = datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc)
        self.service.ingest_report_candidates(
            report=_ReportFixture(
                report_id="r1",
                user_id="user-1",
                data_scope=_ScopeFixture(window_start=window_start),
                g8_memory_candidates=[
                    G8MemoryCandidate(
                        target_layer="L1",
                        candidate_type="episode",
                        summary="Candidate from event.",
                        occurred_at=event_time,
                        confidence=0.7,
                    )
                ],
            ),
            enabled=True,
        )

        candidates = self.repository.list_candidates("user-1")
        self.assertEqual(candidates[0].occurred_at, event_time)

    def test_correct_memory_updates_l1(self) -> None:
        memory_id = self.service.correct_memory(
            user_id="user-1",
            target="L1",
            correction={"episode_id": "ep-1", "summary": "Corrected lunch spike"},
        )

        self.assertEqual(memory_id, "ep-1")
        episode = self.repository.get_episode("ep-1")
        self.assertIsNotNone(episode)
        self.assertEqual(episode.summary, "Corrected lunch spike")

    def test_correct_memory_syncs_l2_when_hermes_home_is_provided(self) -> None:
        hermes_home = Path(self.temp_dir.name) / "hermes"

        memory_id = self.service.correct_memory(
            user_id="user-1",
            target="L2",
            correction={
                "item_id": "pi-1",
                "value": {"summary": "Noodles now have a clearer rise pattern."},
            },
            hermes_home=str(hermes_home),
        )

        self.assertEqual(memory_id, "pi-1")
        content = (hermes_home / "USER.md").read_text(encoding="utf-8")
        self.assertIn("Noodles now have a clearer rise pattern.", content)

    def test_correct_memory_does_not_sync_l2_without_hermes_home(self) -> None:
        hermes_home = Path(self.temp_dir.name) / "unused-hermes"

        memory_id = self.service.correct_memory(
            user_id="user-1",
            target="L2",
            correction={
                "item_id": "pi-1",
                "value": {"summary": "Local correction only."},
            },
        )

        self.assertEqual(memory_id, "pi-1")
        self.assertFalse((hermes_home / "USER.md").exists())

    def test_update_hypothesis_changes_state_and_merges_evidence(self) -> None:
        saved = self.service.update_hypothesis(
            user_id="user-1",
            hypothesis_id="hyp-1",
            state="observing",
            evidence_refs=[{"kind": "aggregate", "ref_id": "agg-1"}],
        )

        self.assertEqual(saved.state, HypothesisState.OBSERVING)
        self.assertEqual(saved.evidence_count, 1)
        self.assertEqual(saved.evidence_refs[0].ref_id, "agg-1")

    def test_update_hypothesis_is_user_scoped(self) -> None:
        with self.assertRaisesRegex(KeyError, "Unknown hypothesis"):
            self.service.update_hypothesis(
                user_id="other-user",
                hypothesis_id="hyp-1",
                state="observing",
            )

        unchanged = self.repository.list_hypotheses(
            "user-1",
            states=[HypothesisState.ARCHIVED],
        )[0]
        self.assertEqual(unchanged.state, HypothesisState.ARCHIVED)

    def test_update_hypothesis_rejects_non_schema_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "state must be one of"):
            self.service.update_hypothesis(
                user_id="user-1",
                hypothesis_id="hyp-1",
                state="OBSERVING",
            )

    def test_update_hypothesis_rejects_non_list_evidence_refs(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence_refs must be a list"):
            self.service.update_hypothesis(
                user_id="user-1",
                hypothesis_id="hyp-1",
                state="observing",
                evidence_refs={"kind": "event", "ref_id": "evt-1"},
            )


@dataclass(frozen=True)
class _ReportFixture:
    report_id: str
    user_id: str
    g8_memory_candidates: list[G8MemoryCandidate]
    data_scope: object | None = None


@dataclass(frozen=True)
class _ScopeFixture:
    window_start: datetime


if __name__ == "__main__":
    unittest.main()
