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

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hermes_cgm_agent.domain import EvidenceRef, HypothesisState, L2ProfileItem
from hermes_cgm_agent.domain.report import AuthoritativeContext, MemoryContext
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.retrieval import (
    HybridRetriever,
    MemoryDoc,
    build_personal_retriever,
)

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
        # The personal L1 retriever depends on episode count (D036), so the
        # default is built lazily after loading the user's episodes.
        pass

    def build_memory_context(
        self,
        *,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> MemoryContext:
        items: list[dict] = []

        # ── Hot (D029): profile (L2) + active hypotheses (L3) are small and
        # high-signal — inject them in full, directly from SQLite. Running a
        # retriever over a handful of structured rows is over-engineering and
        # can silently drop a relevant belief.
        for profile in self.repository.list_profile_items(user_id):
            summary = _profile_summary(profile)
            items.append(
                {
                    "summary": summary,
                    "layer": "L2",
                    "score": 1.0,
                    "matched": True,
                    "hot": True,
                    "evidence_refs": [
                        EvidenceRef(
                            kind="user_memory", ref_id=profile.item_id, summary=summary
                        ).model_dump(mode="json")
                    ],
                }
            )
        active_hypotheses = [
            h
            for h in self.repository.list_hypotheses(user_id)
            if h.state in (HypothesisState.OBSERVING, HypothesisState.STABLE)
        ]
        for hyp in active_hypotheses:
            items.append(
                {
                    "summary": hyp.statement,
                    "layer": "L3",
                    "score": 1.0,
                    "matched": True,
                    "hot": True,
                    "evidence_refs": [
                        EvidenceRef(
                            kind="user_memory", ref_id=hyp.hypothesis_id, summary=hyp.statement
                        ).model_dump(mode="json")
                    ],
                }
            )

        # ── Cold (D029): L1 episodes grow unboundedly over time — this is the
        # only personal store that warrants retrieval. Most-recent first so the
        # recency fallback surfaces fresh memory when a generic query (periodic
        # review) does not lexically match.
        episodes = sorted(
            self.repository.list_episodes(user_id),
            key=lambda e: e.occurred_at,
            reverse=True,
        )
        if episodes:
            docs = [
                MemoryDoc(doc_id=f"L1:{ep.episode_id}", text=ep.summary, layer="L1")
                for ep in episodes
            ]
            ref_index = {
                f"L1:{ep.episode_id}": EvidenceRef(
                    kind="user_memory", ref_id=ep.episode_id, summary=ep.summary
                )
                for ep in episodes
            }
            retriever = self.retriever or build_personal_retriever(
                episode_count=len(episodes)
            )
            results = retriever.retrieve(query, docs, top_k=top_k)
            ordered_ids = [r.doc.doc_id for r in results]
            scores = {r.doc.doc_id: round(r.score, 6) for r in results}
            if len(ordered_ids) < top_k:
                for doc in docs:
                    if doc.doc_id not in scores:
                        ordered_ids.append(doc.doc_id)
                    if len(ordered_ids) >= top_k:
                        break
            by_doc = {doc.doc_id: doc for doc in docs}
            for doc_id in ordered_ids[:top_k]:
                if doc_id in scores:
                    self.repository.touch_episode(doc_id.removeprefix("L1:"))
                items.append(
                    {
                        "summary": by_doc[doc_id].text,
                        "layer": "L1",
                        "score": scores.get(doc_id, 0.0),
                        "matched": doc_id in scores,
                        "hot": False,
                        "evidence_refs": [ref_index[doc_id].model_dump(mode="json")],
                    }
                )

        if not items:
            return MemoryContext(enabled=True, items=[], missing_reason="no_user_memory_yet")
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
                "citation": r.get("citation") or {},
                "verified": r.get("verified"),
                "tier": r.get("tier"),
                "population": r.get("population"),
                "evidence_refs": [r["evidence_ref"]],
            }
            for r in results
        ]
        return AuthoritativeContext(enabled=True, documents=documents)


def _profile_summary(item: L2ProfileItem) -> str:
    """Human-readable one-liner for a directly-injected L2 profile item."""
    value = item.value or {}
    for key in ("summary", "statement", "text", "description"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    if value:
        return f"{item.key}: " + json.dumps(value, ensure_ascii=False, sort_keys=True)
    return item.key
