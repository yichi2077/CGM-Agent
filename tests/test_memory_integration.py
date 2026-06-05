from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import CandidateStatus, EvidenceRef, GlucosePoint, L1Episode
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import (
    CGMMemoryProvider,
    MemoryContextAssembler,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


class MemoryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.cgm = SQLiteCGMRepository(self.store)
        self.mem = SQLiteMemoryRepository(self.store)
        self.session_id = "integration"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_points(self) -> None:
        for i, v in enumerate([90, 100, 150, 190]):
            self.cgm.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, i, 0, tzinfo=timezone.utc),
                    value=v,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )

    def _seed_episode(self) -> None:
        self.mem.create_episode(
            L1Episode(
                episode_id=new_id(),
                user_id="user-1",
                occurred_at=NOW,
                episode_type="postprandial_spike",
                summary="Lunch caused a glucose spike after high carb meal",
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev-1")],
                confidence=0.8,
            )
        )

    def test_assembler_builds_dual_track_contexts_with_correct_kinds(self) -> None:
        self._seed_episode()
        assembler = MemoryContextAssembler(repository=self.mem)

        mem_ctx = assembler.build_memory_context(user_id="user-1", query="lunch spike")
        auth_ctx = assembler.build_authoritative_context(query="time in range")

        self.assertTrue(mem_ctx.items)
        self.assertEqual(
            mem_ctx.items[0]["evidence_refs"][0]["kind"], "user_memory"
        )
        self.assertTrue(auth_ctx.documents)
        self.assertEqual(
            auth_ctx.documents[0]["evidence_refs"][0]["kind"], "authoritative_kb"
        )

    def test_report_with_retrieve_context_injects_but_keeps_facts(self) -> None:
        self._seed_points()
        self._seed_episode()
        executor = ToolExecutor(
            repository=self.cgm,
            audit_service=AuditService(self.store),
        )

        body = executor.execute(
            tool_name="reports.generate",
            arguments={
                "report_type": "daily",
                "user_id": "user-1",
                "retrieve_context": True,
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            },
            session_id=self.session_id,
        ).to_dict()

        self.assertEqual(body["status"], "ok")
        observations = next(
            s for s in body["sections"] if s["section_id"] == "observations"
        )
        # user_memory + authoritative tracks both surfaced and kept distinct
        self.assertIn("user_memory", observations["source_tracks"])
        self.assertIn("authoritative", observations["source_tracks"])
        kinds = {ref["kind"] for ref in observations["evidence_refs"]}
        self.assertIn("user_memory", kinds)
        self.assertIn("authoritative_kb", kinds)
        # facts untouched: metrics section still present and analytics-derived
        metrics = next(s for s in body["sections"] if s["section_id"] == "metrics")
        self.assertTrue(metrics["content"])
        self.assertTrue(any(ref["kind"] == "aggregate" for ref in metrics["evidence_refs"]))

    def test_provider_contract_shape_and_prefetch(self) -> None:
        self._seed_episode()
        provider = CGMMemoryProvider(self.store, user_id="user-1")
        provider.initialize(session_id=self.session_id, user_id="user-1")

        self.assertEqual(provider.name, "cgm_memory")
        self.assertTrue(provider.is_available())
        schemas = provider.get_tool_schemas()
        self.assertEqual(
            {s["name"] for s in schemas},
            {"memory.list", "memory.delete", "memory.confirm", "memory.correct"},
        )
        recall = provider.prefetch("lunch spike")
        self.assertIn("user-memory recall", recall)

    def test_provider_sync_turn_and_precompress_preserve_relevant_context(self) -> None:
        provider = CGMMemoryProvider(self.store, user_id="user-1")
        provider.initialize(
            session_id=self.session_id,
            user_id="user-1",
            hermes_home=self.temp_dir.name,
            platform="cli",
            agent_context="primary",
        )

        provider.sync_turn(
            "After dinner my blood sugar spiked above 220 and I had to walk.",
            "Noted.",
            session_id=self.session_id,
        )
        pending = self.mem.list_candidates("user-1", status=CandidateStatus.PENDING)
        digest = provider.on_pre_compress([])

        self.assertEqual(len(pending), 1)
        self.assertIn("blood sugar spiked", pending[0].summary)
        self.assertIn("Recent conversation notes:", digest)
        self.assertIn("blood sugar spiked", digest)


if __name__ == "__main__":
    unittest.main()
