"""Hybrid memory retrieval: sparse (BM25) + dense (vector) + RRF fusion.

MEM-ARCH-20260601 §5.1 / DECISION_LOG D025. Self-built for very-long-term
memory; no heavy external dependency (the project is intentionally dependency
-light). The dense path uses a pluggable ``Embedder`` interface so a real
sentence-transformer / sqlite-vec backend can be injected later without changing
callers; the default is a deterministic offline embedder so tests are stable.

Fusion is Reciprocal Rank Fusion (RRF, k=60, ranks not scores) — the production
standard that avoids score-normalization issues between BM25 and cosine.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9]+")
RRF_K = 60


@dataclass(frozen=True)
class MemoryDoc:
    """A retrievable unit (an L1 episode, L2 item, or L3 hypothesis)."""

    doc_id: str
    text: str
    layer: str  # "L1" | "L2" | "L3"


@dataclass
class RetrievalResult:
    doc: MemoryDoc
    score: float
    sparse_rank: int | None = None
    dense_rank: int | None = None


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# -- Embedder interface ------------------------------------------------------


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic, dependency-free embedding for offline/dev use.

    Bag-of-tokens hashed into a fixed-width vector with L2 normalization. Good
    enough to make the dense path exercise real cosine behavior in tests; swap
    for a real model in production via the same interface.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# -- BM25 (sparse) -----------------------------------------------------------


class BM25Index:
    def __init__(self, docs: Sequence[MemoryDoc], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self._tokens = [tokenize(d.text) for d in self.docs]
        self._doc_len = [len(t) for t in self._tokens]
        self._avg_len = (sum(self._doc_len) / len(self._doc_len)) if self._doc_len else 0.0
        self._tf = [Counter(t) for t in self._tokens]
        self._df: Counter[str] = Counter()
        for toks in self._tokens:
            for term in set(toks):
                self._df[term] += 1
        self._n = len(self.docs)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str) -> list[tuple[MemoryDoc, float]]:
        q_terms = tokenize(query)
        scored: list[tuple[MemoryDoc, float]] = []
        for i, doc in enumerate(self.docs):
            tf = self._tf[i]
            dl = self._doc_len[i]
            score = 0.0
            for term in q_terms:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * (dl / self._avg_len if self._avg_len else 0))
                score += self._idf(term) * (freq * (self.k1 + 1)) / denom
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# -- Hybrid retriever --------------------------------------------------------


@dataclass
class HybridRetriever:
    embedder: Embedder = field(default_factory=HashingEmbedder)
    rrf_k: int = RRF_K

    def retrieve(
        self,
        query: str,
        docs: Sequence[MemoryDoc],
        *,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        if not docs:
            return []
        # sparse
        bm25 = BM25Index(docs)
        sparse_ranked = bm25.search(query)
        sparse_rank = {doc.doc_id: rank for rank, (doc, _) in enumerate(sparse_ranked, start=1)}
        # dense
        q_vec = self.embedder.embed(query)
        dense_scored = [
            (doc, cosine(q_vec, self.embedder.embed(doc.text))) for doc in docs
        ]
        dense_scored = [d for d in dense_scored if d[1] > 0]
        dense_scored.sort(key=lambda x: x[1], reverse=True)
        dense_rank = {doc.doc_id: rank for rank, (doc, _) in enumerate(dense_scored, start=1)}
        # RRF fusion
        by_id = {doc.doc_id: doc for doc in docs}
        fused: dict[str, float] = {}
        for doc_id in set(sparse_rank) | set(dense_rank):
            score = 0.0
            if doc_id in sparse_rank:
                score += 1.0 / (self.rrf_k + sparse_rank[doc_id])
            if doc_id in dense_rank:
                score += 1.0 / (self.rrf_k + dense_rank[doc_id])
            fused[doc_id] = score
        results = [
            RetrievalResult(
                doc=by_id[doc_id],
                score=score,
                sparse_rank=sparse_rank.get(doc_id),
                dense_rank=dense_rank.get(doc_id),
            )
            for doc_id, score in fused.items()
        ]
        # deterministic ordering: fused score desc, then doc_id for ties
        results.sort(key=lambda r: (-r.score, r.doc.doc_id))
        return results[:top_k]
