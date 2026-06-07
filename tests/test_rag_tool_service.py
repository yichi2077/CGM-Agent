from __future__ import annotations

import unittest

from hermes_cgm_agent.services.rag import AuthoritativeRAGService, AuthoritativeRAGToolService
from hermes_cgm_agent.services.rag.authoritative import ClaimCard, KnowledgeBase


class AuthoritativeRAGToolServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        kb = KnowledgeBase(
            kb_version="kb-tool-test",
            cards=[
                ClaimCard(
                    card_id="tir-general",
                    title="TIR general",
                    claim_zh="大多数成人目标范围内时间通常应超过70%。",
                    claim_en="For most adults, time in range is usually above 70 percent.",
                    population="general",
                    tags=["tir", "target"],
                    source={"citation": "Test KB", "page": 1},
                ),
                ClaimCard(
                    card_id="tir-pregnancy",
                    title="TIR pregnancy",
                    claim_zh="妊娠T1D目标范围内时间通常应超过70%。",
                    claim_en="For pregnancy with type 1 diabetes, time in range is above 70 percent.",
                    population="pregnancy-t1d",
                    tags=["tir", "pregnancy"],
                    source={"citation": "Test KB", "page": 2},
                ),
            ],
        )
        self.service = AuthoritativeRAGToolService(
            AuthoritativeRAGService(knowledge_base=kb)
        )

    def test_search_returns_payload_and_authoritative_evidence_refs(self) -> None:
        result = self.service.search({"query": "time in range target", "top_k": 1})

        self.assertEqual(result.kb_version, "kb-tool-test")
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.payload["kb_version"], "kb-tool-test")
        self.assertEqual(result.payload["quote_instruction"], "verbatim_only")
        self.assertTrue(result.evidence_refs)
        self.assertTrue(
            all(ref["kind"] == "authoritative_kb" for ref in result.evidence_refs)
        )

    def test_search_filters_population_but_keeps_general_cards(self) -> None:
        result = self.service.search(
            {
                "query": "pregnancy time in range target",
                "top_k": 5,
                "population": " pregnancy-t1d ",
            }
        )

        populations = {document["population"] for document in result.documents}
        self.assertTrue(populations <= {"pregnancy-t1d", "general"})

    def test_search_rejects_empty_query(self) -> None:
        with self.assertRaisesRegex(ValueError, "query must be a non-empty string"):
            self.service.search({"query": "   "})

    def test_search_rejects_string_top_k(self) -> None:
        with self.assertRaisesRegex(ValueError, "top_k must be an integer"):
            self.service.search({"query": "time in range", "top_k": "2"})

    def test_search_adds_query_number_coverage_hint(self) -> None:
        result = self.service.search({"query": "time in range above 95", "top_k": 1})

        self.assertEqual(result.payload["query_number_coverage"]["mode"], "coverage")
        self.assertIn(
            "number 95 lacks authoritative evidence mapping",
            result.payload["query_number_coverage"]["uncovered"],
        )


if __name__ == "__main__":
    unittest.main()
