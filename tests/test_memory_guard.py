from __future__ import annotations

import unittest

from hermes_cgm_agent.services.rag import AuthoritativeRAGService
from hermes_cgm_agent.services.safety import (
    MemoryTrackViolation,
    assert_kb_readonly,
    assert_track_isolation,
    resolve_conflict,
)


class MemoryGuardTests(unittest.TestCase):
    def test_clean_tracks_pass(self) -> None:
        assert_track_isolation(
            memory_items=[{"evidence_refs": [{"kind": "user_memory"}]}],
            authoritative_documents=[{"evidence_refs": [{"kind": "authoritative_kb"}]}],
        )

    def test_authoritative_leak_into_memory_raises(self) -> None:
        with self.assertRaises(MemoryTrackViolation):
            assert_track_isolation(
                memory_items=[{"evidence_refs": [{"kind": "authoritative_kb"}]}],
                authoritative_documents=[],
            )

    def test_personal_leak_into_authoritative_raises(self) -> None:
        with self.assertRaises(MemoryTrackViolation):
            assert_track_isolation(
                memory_items=[],
                authoritative_documents=[{"evidence_refs": [{"kind": "user_memory"}]}],
            )

    def test_kb_is_read_only(self) -> None:
        # One-way write protection: the medical KB exposes no mutation API, so
        # personal memory can never be written into it (D031).
        assert_kb_readonly(AuthoritativeRAGService())

    def test_kb_service_construction_enforces_read_only(self) -> None:
        # R2-3: the invariant is enforced at construction, not only when
        # assert_kb_readonly is called manually. A subclass that adds a mutator
        # must fail to construct.
        class MutableKB(AuthoritativeRAGService):
            def delete(self, *args, **kwargs):  # forbidden mutator
                return None

        with self.assertRaises(MemoryTrackViolation):
            MutableKB()

    def test_conflict_resolves_to_authoritative(self) -> None:
        decision = resolve_conflict(
            authoritative={"text": "TIR target >70%"},
            personal={"summary": "用户认为 50% 就够了"},
        )
        self.assertEqual(decision.winner, "authoritative")
        self.assertIsNotNone(decision.personal)
        self.assertTrue(decision.note)


if __name__ == "__main__":
    unittest.main()
