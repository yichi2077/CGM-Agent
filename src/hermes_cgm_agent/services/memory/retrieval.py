"""Hybrid memory retrieval: sparse (BM25) + dense (vector) + RRF fusion.

MEM-ARCH-20260601 §5.1 / DECISION_LOG D025. The dense path supports a real
multilingual sentence-transformer for bilingual semantic recall, but runtime
activation is explicit so Hermes project loading never blocks on accidental
first-run model downloads. A deterministic hashing embedder remains the
no-dependency default for offline/dev environments and stable tests.

Fusion is Reciprocal Rank Fusion (RRF, k=60, ranks not scores) — the production
standard that avoids score-normalization issues between BM25 and cosine.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile("[一-鿿]+")
RRF_K = 60

DEFAULT_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBED_MODEL_ENV = "CGM_AGENT_EMBED_MODEL"
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_MODEL_ENV = "CGM_AGENT_RERANK_MODEL"
USE_HASHING_ENV = "CGM_AGENT_USE_HASHING_EMBEDDER"
ENABLE_SEMANTIC_ENV = "CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL"
PERSONAL_SEMANTIC_MIN_EPISODES_ENV = "CGM_AGENT_PERSONAL_SEMANTIC_MIN_EPISODES"
DEFAULT_PERSONAL_SEMANTIC_MIN_EPISODES = 200

_model_lock = threading.Lock()
_st_model_cache: dict[str, object] = {}
_ce_model_cache: dict[str, object] = {}
_warned = {
    "embed_fallback": False,
    "rerank_fallback": False,
}


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
    rerank_score: float | None = None


def tokenize(text: str) -> list[str]:
    # ASCII words (lower-cased) + CJK character bigrams. Chinese has no
    # whitespace word boundaries, so a segmenter-free BM25 indexes character
    # bigrams (the standard approach); this gives the medical KB and personal
    # memory cross-lingual lexical recall (D030) — a 中文 query like "目标范围"
    # matches a card's Chinese text. Single-char CJK runs fall back to a unigram.
    tokens = _TOKEN_RE.findall(text.lower())
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def rerank(self, query: str, docs: Sequence[MemoryDoc]) -> list[tuple[str, float]]: ...


class HashingEmbedder:
    """Deterministic, dependency-free embedding for offline/dev use."""

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

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class SentenceTransformerEmbedder:
    """Local sentence-transformer embedder with process-wide model caching."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.environ.get(EMBED_MODEL_ENV, DEFAULT_EMBED_MODEL)

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        model = _load_sentence_transformer(self.model_name)
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [_to_float_list(vector) for vector in vectors]


class CrossEncoderReranker:
    """Optional reranker for the final candidate set."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.environ.get(RERANK_MODEL_ENV, DEFAULT_RERANK_MODEL)

    def rerank(self, query: str, docs: Sequence[MemoryDoc]) -> list[tuple[str, float]]:
        model = _load_cross_encoder(self.model_name)
        pairs = [[query, doc.text] for doc in docs]
        scores = model.predict(pairs, show_progress_bar=False)
        scored = [
            (doc.doc_id, float(score))
            for doc, score in zip(docs, scores, strict=True)
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored


def build_default_embedder() -> Embedder | None:
    # P2 / D029+D030: HashingEmbedder is no longer an implicit "dense" default.
    # A SHA1 token-hash bag is not semantic — it only gave the hybrid retriever a
    # degenerate second lexical signal that collides and adds no cross-lingual
    # recall. The dense path now activates ONLY when real semantic retrieval is
    # configured (a cross-lingual sentence-transformer, D030). Otherwise we run
    # sparse-only (BM25). HashingEmbedder stays available but is returned
    # ONLY when explicitly forced (offline / deterministic tests via USE_HASHING_ENV).
    if _use_hashing_embedder_forced():
        return HashingEmbedder()
    if not _semantic_retrieval_enabled():
        return None
    if _sentence_transformers_available():
        return SentenceTransformerEmbedder()
    if not _warned["embed_fallback"]:
        logger.warning(
            "Semantic retrieval was explicitly enabled, but sentence-transformers "
            "is not available; running sparse-only (no dense path)."
        )
        _warned["embed_fallback"] = True
    return None


def build_default_reranker() -> Reranker | None:
    if _use_hashing_embedder_forced():
        return None
    if not _semantic_retrieval_enabled():
        return None
    if _sentence_transformers_available():
        return CrossEncoderReranker()
    if not _warned["rerank_fallback"]:
        logger.info(
            "Semantic retrieval was explicitly enabled, but sentence-transformers "
            "is not available; skipping cross-encoder reranking."
        )
        _warned["rerank_fallback"] = True
    return None


def build_authoritative_retriever() -> "HybridRetriever":
    """Authoritative KB retriever: sparse-only by policy (D036).

    The medical KB is a small, curated claim-card corpus. Keeping this path
    BM25-only makes retrieval explainable and avoids accidental model loading in
    Hermes plugin startup.
    """

    return HybridRetriever(embedder=None, reranker=None)


def build_personal_retriever(*, episode_count: int = 0) -> "HybridRetriever":
    """Personal L1 retriever: sparse at small scale, semantic when it matters.

    L2/L3 are injected hot and never use this path. L1 grows over time and
    becomes increasingly paraphrase-heavy, so semantic retrieval is enabled when
    explicitly configured or when the episode count crosses the D036 threshold.
    """

    if _use_hashing_embedder_forced() or _semantic_retrieval_enabled():
        return HybridRetriever()
    if episode_count >= _personal_semantic_min_episodes():
        if _sentence_transformers_available():
            return HybridRetriever(
                embedder=SentenceTransformerEmbedder(),
                reranker=CrossEncoderReranker(),
            )
        if not _warned["embed_fallback"]:
            logger.warning(
                "Personal L1 semantic retrieval threshold was reached, but "
                "sentence-transformers is not available; running sparse-only."
            )
            _warned["embed_fallback"] = True
    return HybridRetriever(embedder=None, reranker=None)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


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
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored


@dataclass
class HybridRetriever:
    embedder: Embedder | None = field(default_factory=build_default_embedder)
    reranker: Reranker | None = field(default_factory=build_default_reranker)
    rrf_k: int = RRF_K
    rerank_candidates: int = 8

    def retrieve(
        self,
        query: str,
        docs: Sequence[MemoryDoc],
        *,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        if not docs:
            return []

        bm25 = BM25Index(docs)
        sparse_ranked = bm25.search(query)
        sparse_rank = {doc.doc_id: rank for rank, (doc, _) in enumerate(sparse_ranked, start=1)}

        # Dense path is optional (P2): runs only when a real (semantic) embedder
        # is configured. With embedder=None the retriever is sparse-only and RRF
        # degenerates to BM25 rank order.
        dense_rank: dict[str, int] = {}
        if self.embedder is not None:
            q_vec = self.embedder.embed(query)
            doc_vectors = self._embed_docs(docs)
            dense_scored = [
                (doc, cosine(q_vec, vector))
                for doc, vector in zip(docs, doc_vectors, strict=True)
            ]
            dense_scored = [item for item in dense_scored if item[1] > 0]
            dense_scored.sort(key=lambda item: item[1], reverse=True)
            dense_rank = {doc.doc_id: rank for rank, (doc, _) in enumerate(dense_scored, start=1)}

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
        results.sort(key=lambda result: (-result.score, result.doc.doc_id))

        if self.reranker and len(results) > 1:
            results = self._apply_reranker(query, results)

        return results[:top_k]

    def _embed_docs(self, docs: Sequence[MemoryDoc]) -> list[list[float]]:
        texts = [doc.text for doc in docs]
        if hasattr(self.embedder, "embed_many"):
            return self.embedder.embed_many(texts)
        return [self.embedder.embed(text) for text in texts]

    def _apply_reranker(self, query: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
        candidate_count = min(max(self.rerank_candidates, 2), len(results))
        candidates = results[:candidate_count]
        reranked = self.reranker.rerank(query, [result.doc for result in candidates])
        score_by_id = {doc_id: score for doc_id, score in reranked}

        reranked_candidates = [
            RetrievalResult(
                doc=result.doc,
                score=result.score,
                sparse_rank=result.sparse_rank,
                dense_rank=result.dense_rank,
                rerank_score=score_by_id.get(result.doc.doc_id),
            )
            for result in candidates
        ]
        reranked_candidates.sort(
            key=lambda result: (
                -(result.rerank_score if result.rerank_score is not None else float("-inf")),
                -result.score,
                result.doc.doc_id,
            )
        )
        tail = results[candidate_count:]
        return reranked_candidates + tail


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _use_hashing_embedder_forced() -> bool:
    return _truthy_env(USE_HASHING_ENV)


def _semantic_retrieval_enabled() -> bool:
    if _use_hashing_embedder_forced():
        return False
    if _truthy_env(ENABLE_SEMANTIC_ENV):
        return True
    return bool(
        os.environ.get(EMBED_MODEL_ENV, "").strip()
        or os.environ.get(RERANK_MODEL_ENV, "").strip()
    )


def _personal_semantic_min_episodes() -> int:
    raw = os.environ.get(PERSONAL_SEMANTIC_MIN_EPISODES_ENV, "").strip()
    if not raw:
        return DEFAULT_PERSONAL_SEMANTIC_MIN_EPISODES
    try:
        threshold = int(raw)
    except ValueError:
        return DEFAULT_PERSONAL_SEMANTIC_MIN_EPISODES
    return max(1, threshold)


def _load_sentence_transformer(model_name: str) -> Any:
    with _model_lock:
        cached = _st_model_cache.get(model_name)
        if cached is not None:
            return cached
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name)
        _st_model_cache[model_name] = model
        return model


def _load_cross_encoder(model_name: str) -> Any:
    with _model_lock:
        cached = _ce_model_cache.get(model_name)
        if cached is not None:
            return cached
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(model_name)
        _ce_model_cache[model_name] = model
        return model


def _to_float_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]
