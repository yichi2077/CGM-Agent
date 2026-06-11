"""F3-B2 / US2: clinical sign-off via ``kb.approve``.

``AuthoritativeRAGService.approve`` is the ONLY sanctioned KB write path. It sets
``verified=true`` with ``reviewer`` + ``reviewed_at`` provenance, restricted to
``tier=curated`` cards, and the KB validator continues to reject any verified
card lacking provenance (contract C2 / SC-002 / SC-006).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.rag.authoritative import (
    AuthoritativeRAGService,
    load_knowledge_base,
)
from hermes_cgm_agent.services.rag.validator import validate_knowledge_base


def _curated(card_id: str = "c-tir") -> dict:
    return {
        "card_id": card_id,
        "title": "TIR 目标",
        "claim_zh": "目标范围内时间建议维持在 70% 以上。",
        "claim_en": "Keep time in range above 70 percent.",
        "synonyms": [],
        "source": {"citation": "ADA Standards 2024", "page": 10},
        "verified": False,
        "tier": "curated",
    }


def _auto(card_id: str = "a-draft") -> dict:
    return {
        "card_id": card_id,
        "title": "自动草稿",
        "claim_zh": "这是一张自动摄取的草稿卡片。",
        "claim_en": "This is a machine-ingested draft card.",
        "synonyms": [],
        "source": {"citation": "auto-ingest", "page": 1},
        "verified": False,
        "tier": "auto",
    }


class KbApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.kb_path = Path(self.temp_dir.name) / "kb.json"
        self._write_kb([_curated(), _auto()])
        self.service = AuthoritativeRAGService(kb_path=self.kb_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_kb(self, cards: list[dict]) -> None:
        self.kb_path.write_text(
            json.dumps({"kb_version": "test-kb-v1", "cards": cards}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _reload_card(self, card_id: str) -> dict:
        kb = load_knowledge_base(self.kb_path)
        return next(c for c in kb.cards if c.card_id == card_id).__dict__

    def test_approve_curated_card_sets_verified_and_provenance(self) -> None:
        result = self.service.approve(card_id="c-tir", reviewer="Dr. Wang")
        self.assertTrue(result["verified"])
        self.assertEqual(result["reviewer"], "Dr. Wang")
        self.assertTrue(result["reviewed_at"])
        # Persisted to the KB JSON, not just in memory.
        persisted = self._reload_card("c-tir")
        self.assertTrue(persisted["verified"])
        self.assertEqual(persisted["reviewer"], "Dr. Wang")

    def test_reviewed_at_defaults_to_utc_iso(self) -> None:
        result = self.service.approve(card_id="c-tir", reviewer="Dr. Wang")
        # ISO-8601 UTC timestamp (contains a date separator and a time).
        self.assertRegex(result["reviewed_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_reapprove_same_reviewer_is_idempotent(self) -> None:
        first = self.service.approve(card_id="c-tir", reviewer="Dr. Wang")
        second = self.service.approve(card_id="c-tir", reviewer="Dr. Wang")
        self.assertEqual(second["verified"], True)
        self.assertEqual(second["reviewed_at"], first["reviewed_at"])

    def test_approve_auto_card_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.service.approve(card_id="a-draft", reviewer="Dr. Wang")
        self.assertIn("curated", str(ctx.exception).lower())

    def test_approve_missing_card_errors(self) -> None:
        with self.assertRaises(ValueError):
            self.service.approve(card_id="does-not-exist", reviewer="Dr. Wang")

    def test_missing_reviewer_is_a_validation_error(self) -> None:
        with self.assertRaises(ValueError):
            self.service.approve(card_id="c-tir", reviewer="")

    def test_validator_passes_on_approved_card(self) -> None:
        self.service.approve(card_id="c-tir", reviewer="Dr. Wang")
        problems = validate_knowledge_base(load_knowledge_base(self.kb_path))
        self.assertEqual(problems, [])

    def test_validator_rejects_verified_without_provenance(self) -> None:
        bad = _curated("c-bad")
        bad["verified"] = True  # no reviewer / reviewed_at
        self._write_kb([bad])
        problems = validate_knowledge_base(load_knowledge_base(self.kb_path))
        self.assertTrue(any("provenance" in p for p in problems))

    def test_kb_approve_audit_payload_has_no_claim_text(self) -> None:
        # SEC-003 / T020: the kb.approve audit records provenance only
        # (card_id + reviewer), never the card's claim text.
        import os
        from unittest import mock

        from hermes_cgm_agent.services.data import SQLiteCGMRepository
        from hermes_cgm_agent.services.tools import ToolExecutor
        from hermes_cgm_agent.storage.sqlite import SQLiteStore

        class _Recorder:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            def log(self, *, session_id: str, event_type: str, payload: dict) -> str:
                self.payloads.append(payload)
                return "audit-1"

        db_path = Path(self.temp_dir.name) / "app.db"
        store = SQLiteStore(db_path)
        store.initialize()
        recorder = _Recorder()
        executor = ToolExecutor(
            repository=SQLiteCGMRepository(store), audit_service=recorder
        )
        with mock.patch.dict(os.environ, {"CGM_AGENT_KB_PATH": str(self.kb_path)}):
            response = executor.execute(
                tool_name="kb.approve",
                arguments={"card_id": "c-tir", "reviewer": "Dr. Wang"},
                session_id="s1",
            )
        self.assertEqual(response.status, "ok")
        self.assertTrue(recorder.payloads)
        blob = repr(recorder.payloads[-1])
        self.assertNotIn("目标范围内时间", blob)  # claim_zh body
        self.assertNotIn("Keep time in range", blob)  # claim_en body
        self.assertNotIn("70", blob)
        # Provenance IS recorded (that is the whole point of sign-off).
        self.assertIn("c-tir", blob)
        self.assertIn("Dr. Wang", blob)

    def test_unverified_card_carries_marker_verified_does_not(self) -> None:
        # T014b / FR-006: a verified=false card is surfaced with the unverified
        # marker; after sign-off the marker is gone.
        before = self.service.search("目标范围内时间 time in range", top_k=3)
        tir_before = next(d for d in before if d["doc_id"] == "c-tir")
        self.assertIn("待核验", tir_before["evidence_ref"]["summary"])

        approved = AuthoritativeRAGService(kb_path=self.kb_path)
        approved.approve(card_id="c-tir", reviewer="Dr. Wang")
        fresh = AuthoritativeRAGService(kb_path=self.kb_path)
        after = fresh.search("目标范围内时间 time in range", top_k=3)
        tir_after = next(d for d in after if d["doc_id"] == "c-tir")
        self.assertNotIn("待核验", tir_after["evidence_ref"]["summary"])


if __name__ == "__main__":
    unittest.main()
