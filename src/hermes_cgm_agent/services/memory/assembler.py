"""Assemble retrieval results into report-ready context (MEM-ARCH §7).

Bridges the memory + RAG layers into the G7 report's existing RAG-aware slots
(`memory_context` / `authoritative_context`) WITHOUT letting retrieval override
facts (D013): metrics stay analytics-computed; retrieved items only add
source-tracked, evidence-tagged background.

User-memory track (L1 episodes + active L3 hypotheses) -> MemoryContext, evidence
kind ``user_memory``. Authoritative track -> AuthoritativeContext, evidence kind
``authoritative_kb``. The two are never merged into one track.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hermes_cgm_agent.domain import EvidenceRef, HypothesisState
from hermes_cgm_agent.domain.report import AuthoritativeContext, MemoryContext
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.retrieval import HybridRetriever, MemoryDoc

if TYPE_CHECKING:
    # Imported lazily at call time to avoid a rag <-> memory circular import
    # (rag.authoritative imports memory.retrieval, which pulls memory/__init__).
    from hermes_cgm_agent.services.rag.authoritative import AuthoritativeRAGService


@dataclass
class MemoryContextAssembler:
    repository: SQLiteMemoryRepository
    retriever: HybridRetriever | None = None
    rag_service: AuthoritativeRAGService | None = None

    def __post_init__(self) -> None:
        self.retriever = self.retriever or HybridRetriever()

    def build_memory_context(
        self,
        *,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> MemoryContext:
        # most-recent episodes first so the recency fallback surfaces fresh memory
        episodes = sorted(
            self.repository.list_episodes(user_id),
            key=lambda e: e.occurred_at,
            reverse=True,
        )
        hypotheses = [
            h
            for h in self.repository.list_hypotheses(user_id)
            if h.state in (HypothesisState.OBSERVING, HypothesisState.STABLE)
        ]
        docs: list[MemoryDoc] = []
        ref_index: dict[str, EvidenceRef] = {}
        for ep in episodes:
            doc_id = f"L1:{ep.episode_id}"
            docs.append(MemoryDoc(doc_id=doc_id, text=ep.summary, layer="L1"))
            ref_index[doc_id] = EvidenceRef(
                kind="user_memory", ref_id=ep.episode_id, summary=ep.summary
            )
        for hyp in hypotheses:
            doc_id = f"L3:{hyp.hypothesis_id}"
            docs.append(MemoryDoc(doc_id=doc_id, text=hyp.statement, layer="L3"))
            ref_index[doc_id] = EvidenceRef(
                kind="user_memory", ref_id=hyp.hypothesis_id, summary=hyp.statement
            )

        if not docs:
            return MemoryContext(enabled=True, items=[], missing_reason="no_user_memory_yet")

        results = self.retriever.retrieve(query, docs, top_k=top_k)
        ordered_ids = [r.doc.doc_id for r in results]
        scores = {r.doc.doc_id: round(r.score, 6) for r in results}
        # Recency fallback: periodic reviews use generic queries that may not
        # lexically match any episode. Backfill with the most recent memory so
        # personal context is still surfaced (it never overrides facts).
        if len(ordered_ids) < top_k:
            for doc in docs:
                if doc.doc_id not in scores:
                    ordered_ids.append(doc.doc_id)
                if len(ordered_ids) >= top_k:
                    break
        by_doc = {doc.doc_id: doc for doc in docs}
        items = [
            {
                "summary": by_doc[doc_id].text,
                "layer": by_doc[doc_id].layer,
                "score": scores.get(doc_id, 0.0),
                "matched": doc_id in scores,
                "evidence_refs": [ref_index[doc_id].model_dump(mode="json")],
            }
            for doc_id in ordered_ids[:top_k]
        ]
        return MemoryContext(enabled=True, items=items)

    def build_authoritative_context(
        self,
        *,
        query: str,
        top_k: int = 3,
    ) -> AuthoritativeContext:
        if self.rag_service is None:
            from hermes_cgm_agent.services.rag.authoritative import (
                AuthoritativeRAGService,
            )

            self.rag_service = AuthoritativeRAGService()
        results = self.rag_service.search(query, top_k=top_k)
        if not results:
            return AuthoritativeContext(
                enabled=True, documents=[], missing_reason="no_authoritative_match"
            )
        documents = [
            {
                "title": r["title"],
                "text": r["text"],
                "kb_version": r["kb_version"],
                "source": r.get("source"),
                "evidence_refs": [r["evidence_ref"]],
            }
            for r in results
        ]
        return AuthoritativeContext(enabled=True, documents=documents)
