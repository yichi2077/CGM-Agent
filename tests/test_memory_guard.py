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
        # One-way write protection: the medical KB exposes no UNSANCTIONED mutation
        # API, so personal memory can never be written into it (D031). The single
        # sanctioned write path — `approve` (clinical sign-off, F3-B2) — is
        # explicitly allowlisted; every other mutator is still rejected.
        assert_kb_readonly(AuthoritativeRAGService(), allow_methods={"approve"})

    def test_kb_service_construction_enforces_read_only(self) -> None:
        # R2-3: the invariant is enforced at construction, not only when
        # assert_kb_readonly is called manually. A subclass that adds a mutator
        # must fail to construct.
        class MutableKB(AuthoritativeRAGService):
            def delete(self, *args, **kwargs):  # forbidden mutator
                return None

        with self.assertRaises(MemoryTrackViolation):
            MutableKB()

    def test_kb_readonly_blocks_existing_mutators(self) -> None:
        # F3-T002(a): the denylist still rejects every pre-existing mutator name.
        for attr in ("add", "write", "insert", "upsert", "update", "delete", "save"):
            obj = type("Mutable", (), {attr: lambda self: None})()
            with self.assertRaises(MemoryTrackViolation):
                assert_kb_readonly(obj)

    def test_kb_readonly_blocks_approve_by_default(self) -> None:
        # F3-T002(b): NEW — an `approve` mutator must be caught by the default
        # denylist so a write method can never silently bypass the guard
        # (analyze I1: the prior denylist did NOT include `approve`).
        obj = type("Mutable", (), {"approve": lambda self: None})()
        with self.assertRaises(MemoryTrackViolation):
            assert_kb_readonly(obj)

    def test_kb_readonly_allowlist_permits_approve_only(self) -> None:
        # F3-T002(c): the explicit allowlist exempts `approve` while every other
        # mutator stays blocked even when an allowlist is supplied.
        approver = type("Approver", (), {"approve": lambda self: None})()
        assert_kb_readonly(approver, allow_methods={"approve"})  # must not raise

        mixed = type(
            "Mixed",
            (),
            {"approve": lambda self: None, "delete": lambda self: None},
        )()
        with self.assertRaises(MemoryTrackViolation):
            assert_kb_readonly(mixed, allow_methods={"approve"})

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
