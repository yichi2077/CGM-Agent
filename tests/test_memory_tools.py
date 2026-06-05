from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef, HypothesisState, L1Episode, L2ProfileItem, L3Hypothesis
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

    def test_memory_delete_removes_specific_record(self) -> None:
        response = self.executor.execute(
            tool_name="memory.delete",
            arguments={"user_id": "user-1", "memory_id": "hyp-1", "layer": "L3"},
            session_id="memory-tools",
        ).to_dict()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["deleted_id"], "hyp-1")
        self.assertEqual(self.memory.list_hypotheses("user-1", states=[HypothesisState.ARCHIVED]), [])
