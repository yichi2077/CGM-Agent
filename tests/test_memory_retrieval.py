from __future__ import annotations

import unittest

from hermes_cgm_agent.services.memory import (
    BM25Index,
    HashingEmbedder,
    HybridRetriever,
    MemoryDoc,
)


DOCS = [
    MemoryDoc("d1", "Post lunch glucose spike after high carb meal", "L1"),
    MemoryDoc("d2", "Overnight low blood sugar around 3am", "L1"),
    MemoryDoc("d3", "Exercise in the afternoon lowered glucose", "L1"),
    MemoryDoc("d4", "Friday dinners tend to run high", "L3"),
    MemoryDoc("d5", "User prefers concise morning summaries", "L2"),
]


class HybridRetrievalTests(unittest.TestCase):
    def test_bm25_exact_term_match(self) -> None:
        index = BM25Index(DOCS)
        results = index.search("overnight low")
        self.assertTrue(results)
        self.assertEqual(results[0][0].doc_id, "d2")

    def test_hashing_embedder_is_deterministic_and_normalized(self) -> None:
        emb = HashingEmbedder()
        a = emb.embed("post lunch spike")
        b = emb.embed("post lunch spike")
        self.assertEqual(a, b)  # stable across calls (sha1-based, not salted hash)
        norm = sum(v * v for v in a) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_hybrid_retrieve_ranks_relevant_doc_first(self) -> None:
        retriever = HybridRetriever()
        results = retriever.retrieve("carb meal glucose spike", DOCS, top_k=3)
        self.assertTrue(results)
        self.assertEqual(results[0].doc.doc_id, "d1")
        # fused result carries provenance of both channels where applicable
        self.assertIsNotNone(results[0].sparse_rank)

    def test_hybrid_is_deterministic(self) -> None:
        retriever = HybridRetriever()
        first = retriever.retrieve("friday high dinner", DOCS, top_k=5)
        second = retriever.retrieve("friday high dinner", DOCS, top_k=5)
        self.assertEqual([r.doc.doc_id for r in first], [r.doc.doc_id for r in second])

    def test_empty_docs_returns_empty(self) -> None:
        self.assertEqual(HybridRetriever().retrieve("anything", [], top_k=5), [])

    def test_dense_recall_when_sparse_misses_exact_terms(self) -> None:
        # Query shares no exact tokens with d3 except via overlap; ensure hybrid
        # still returns candidates (dense channel contributes) without crashing.
        retriever = HybridRetriever()
        results = retriever.retrieve("afternoon workout reduced sugar", DOCS, top_k=3)
        self.assertTrue(results)
        self.assertIn("d3", [r.doc.doc_id for r in results])


if __name__ == "__main__":
    unittest.main()
