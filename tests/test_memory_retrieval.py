from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from hermes_cgm_agent.services.memory import (
    BM25Index,
    HashingEmbedder,
    HybridRetriever,
    MemoryDoc,
    build_authoritative_retriever,
    build_personal_retriever,
)
from hermes_cgm_agent.services.memory.retrieval import (
    CrossEncoderReranker,
    SentenceTransformerEmbedder,
    build_default_embedder,
    build_default_reranker,
)


DOCS = [
    MemoryDoc("d1", "Post lunch glucose spike after high carb meal", "L1"),
    MemoryDoc("d2", "Overnight low blood sugar around 3am", "L1"),
    MemoryDoc("d3", "Exercise in the afternoon lowered glucose", "L1"),
    MemoryDoc("d4", "Friday dinners tend to run high", "L3"),
    MemoryDoc("d5", "User prefers concise morning summaries", "L2"),
]


class HybridRetrievalTests(unittest.TestCase):
    def test_default_embedder_is_sparse_only_without_opt_in(self) -> None:
        # P2 / D029+D030: no implicit HashingEmbedder. Without an explicit
        # semantic opt-in the dense path is disabled (sparse-only BM25).
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "hermes_cgm_agent.services.memory.retrieval._sentence_transformers_available",
                return_value=True,
            ):
                self.assertIsNone(build_default_embedder())
                self.assertIsNone(build_default_reranker())

    def test_hashing_embedder_only_when_explicitly_forced(self) -> None:
        with patch.dict(
            os.environ,
            {"CGM_AGENT_USE_HASHING_EMBEDDER": "1"},
            clear=True,
        ):
            self.assertIsInstance(build_default_embedder(), HashingEmbedder)

    def test_semantic_retrieval_can_be_explicitly_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {"CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL": "1"},
            clear=True,
        ):
            with patch(
                "hermes_cgm_agent.services.memory.retrieval._sentence_transformers_available",
                return_value=True,
            ):
                self.assertIsInstance(build_default_embedder(), SentenceTransformerEmbedder)
                self.assertIsInstance(build_default_reranker(), CrossEncoderReranker)

    def test_authoritative_retriever_is_sparse_only_even_when_semantic_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {"CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL": "1"},
            clear=True,
        ):
            with patch(
                "hermes_cgm_agent.services.memory.retrieval._sentence_transformers_available",
                return_value=True,
            ):
                retriever = build_authoritative_retriever()
        self.assertIsNone(retriever.embedder)
        self.assertIsNone(retriever.reranker)

    def test_personal_retriever_enables_semantic_after_episode_threshold(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "hermes_cgm_agent.services.memory.retrieval._sentence_transformers_available",
                return_value=True,
            ):
                small = build_personal_retriever(episode_count=10)
                large = build_personal_retriever(episode_count=201)
        self.assertIsNone(small.embedder)
        self.assertIsInstance(large.embedder, SentenceTransformerEmbedder)
        self.assertIsInstance(large.reranker, CrossEncoderReranker)

    def test_model_env_also_counts_as_explicit_opt_in(self) -> None:
        with patch.dict(
            os.environ,
            {"CGM_AGENT_EMBED_MODEL": "custom-model"},
            clear=True,
        ):
            with patch(
                "hermes_cgm_agent.services.memory.retrieval._sentence_transformers_available",
                return_value=True,
            ):
                self.assertIsInstance(build_default_embedder(), SentenceTransformerEmbedder)

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

    def test_partial_token_overlap_still_recalls_sparse_only(self) -> None:
        # Sparse-only default (P2): a query sharing only some exact tokens with a
        # doc ("afternoon") still recalls it; full semantic matching is opt-in.
        retriever = HybridRetriever()
        results = retriever.retrieve("afternoon workout reduced sugar", DOCS, top_k=3)
        self.assertTrue(results)
        self.assertIn("d3", [r.doc.doc_id for r in results])


if __name__ == "__main__":
    unittest.main()
