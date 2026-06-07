from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
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
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class MemoryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.memory = SQLiteMemoryRepository(self.store)
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        self.memory.create_episode(
            L1Episode(
                episode_id="ep-1",
                user_id="user-1",
                occurred_at=now,
                episode_type="meal",
                summary="Lunch spike",
                evidence_refs=[EvidenceRef(kind="event", ref_id="evt-1")],
            )
        )
        self.memory.upsert_profile_item(
            L2ProfileItem(
                item_id="pi-1",
                user_id="user-1",
                key="food:noodle",
                value={"pattern": "post_meal_rise"},
            )
        )
        self.memory.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id="hyp-1",
                user_id="user-1",
                statement="Noodles often run high",
                state=HypothesisState.ARCHIVED,
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_memory_list_returns_all_layers(self) -> None:
        response = self.executor.execute(
            tool_name="memory.list",
            arguments={"user_id": "user-1", "layer": "all", "include_archived": True},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["total_count"], 3)
        self.assertEqual({item["layer"] for item in response["memories"]}, {"L1", "L2", "L3"})

    def test_memory_list_rejects_string_include_archived_flag(self) -> None:
        response = self.executor.execute(
            tool_name="memory.list",
            arguments={"user_id": "user-1", "layer": "all", "include_archived": "false"},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "error")
        self.assertIn("include_archived must be a boolean", response["error"])

    def test_memory_list_rejects_lowercase_schema_layer(self) -> None:
        response = self.executor.execute(
            tool_name="memory.list",
            arguments={"user_id": "user-1", "layer": "l1"},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "error")
        self.assertIn("layer must be one of", response["error"])

    def test_memory_list_exposes_pending_candidates_for_review(self) -> None:
        self.memory.enqueue_candidate(
            MemoryCandidate(
                candidate_id="cand-1",
                user_id="user-1",
                target_layer=MemoryLayer.L1,
                candidate_type="episode",
                summary="Dinner walk helped.",
                evidence_refs=[EvidenceRef(kind="event", ref_id="evt-2")],
            )
        )

        response = self.executor.execute(
            tool_name="memory.list",
            arguments={"user_id": "user-1", "layer": "candidates"},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["total_count"], 0)
        self.assertEqual(response["candidate_count"], 1)
        self.assertEqual(response["candidates"][0]["candidate_id"], "cand-1")
        self.assertEqual(response["candidates"][0]["status"], CandidateStatus.PENDING.value)

    def test_memory_list_candidate_status_all_includes_resolved_candidates(self) -> None:
        self.memory.enqueue_candidate(
            MemoryCandidate(
                candidate_id="cand-accepted",
                user_id="user-1",
                target_layer=MemoryLayer.L1,
                candidate_type="episode",
                summary="Accepted candidate.",
            )
        )
        self.memory.set_candidate_status(
            "cand-accepted",
            status=CandidateStatus.ACCEPTED,
        )

        default_response = self.executor.execute(
            tool_name="memory.list",
            arguments={"user_id": "user-1", "layer": "candidates"},
            session_id="memory-tools",
        ).to_dict()
        all_response = self.executor.execute(
            tool_name="memory.list",
            arguments={
                "user_id": "user-1",
                "layer": "candidates",
                "candidate_status": "all",
            },
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(default_response["candidate_count"], 0)
        self.assertEqual(all_response["candidate_count"], 1)
        self.assertEqual(all_response["candidates"][0]["candidate_id"], "cand-accepted")
        self.assertEqual(all_response["candidates"][0]["status"], CandidateStatus.ACCEPTED.value)

    def test_memory_list_rejects_candidate_status_with_whitespace(self) -> None:
        response = self.executor.execute(
            tool_name="memory.list",
            arguments={
                "user_id": "user-1",
                "layer": "candidates",
                "candidate_status": " accepted ",
            },
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "error")
        self.assertIn("candidate_status must be one of", response["error"])

    def test_memory_delete_removes_specific_record(self) -> None:
        response = self.executor.execute(
            tool_name="memory.delete",
            arguments={"user_id": "user-1", "memory_id": "hyp-1", "layer": "L3"},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["deleted_id"], "hyp-1")
        self.assertEqual(self.memory.list_hypotheses("user-1", states=[HypothesisState.ARCHIVED]), [])

    def test_memory_delete_rejects_lowercase_schema_layer(self) -> None:
        response = self.executor.execute(
            tool_name="memory.delete",
            arguments={"user_id": "user-1", "memory_id": "hyp-1", "layer": "l3"},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "error")
        self.assertIn("layer must be one of", response["error"])
