from __future__ import annotations

import unittest

from hermes_cgm_agent.services.rag import (
    ClaimCard,
    KnowledgeBase,
    load_knowledge_base,
    validate_card,
    validate_knowledge_base,
)


def _card(**overrides) -> ClaimCard:
    base = dict(
        card_id="c1",
        title="TIR target",
        claim_zh="目标范围内时间 >70%",
        claim_en="Time in range >70%",
        source={"citation": "Diabetes Care 2019;42:1593-1603", "page": 16},
        verified=False,
    )
    base.update(overrides)
    return ClaimCard(**base)


class KBValidatorTests(unittest.TestCase):
    def test_packaged_draft_kb_passes(self) -> None:
        # All 6 shipped seed cards are verified=false drafts and must validate.
        kb = load_knowledge_base()
        self.assertEqual(validate_knowledge_base(kb), [])
        self.assertTrue(all(c.verified is False for c in kb.cards))

    def test_verified_true_without_reviewer_fails(self) -> None:
        problems = validate_card(_card(verified=True))
        self.assertTrue(any("reviewer" in p or "reviewed_at" in p for p in problems))

    def test_verified_true_with_reviewer_passes(self) -> None:
        self.assertEqual(
            validate_card(_card(verified=True, reviewer="Dr. X", reviewed_at="2026-06-06")),
            [],
        )

    def test_missing_required_field_fails(self) -> None:
        problems = validate_card(_card(claim_en=""))
        self.assertTrue(any("claim_en" in p for p in problems))

    def test_bad_page_type_fails(self) -> None:
        problems = validate_card(_card(source={"citation": "x", "page": "sixteen"}))
        self.assertTrue(any("page" in p for p in problems))

    def test_missing_citation_fails(self) -> None:
        problems = validate_card(_card(source={}))
        self.assertTrue(any("citation" in p or "doc" in p for p in problems))

    def test_duplicate_card_id_detected(self) -> None:
        kb = KnowledgeBase(kb_version="v1", cards=[_card(), _card()])
        self.assertTrue(any("duplicate" in p for p in validate_knowledge_base(kb)))


if __name__ == "__main__":
    unittest.main()
