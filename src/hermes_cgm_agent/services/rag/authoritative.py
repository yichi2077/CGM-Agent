"""Authoritative-knowledge RAG track (MEM-ARCH-20260601 §6; D013/D025).

This is the SECOND track of the dual-track RAG, physically separate from the
user-memory track. Evidence from here is tagged ``authoritative_kb`` and is
high-confidence / citeable; it MUST NOT be mixed with ``user_memory`` evidence.
On conflict, authoritative knowledge wins, but the caller should present it
gently as fact, not as a denial of the user (handled at generation time).

Retrieval reuses the same hybrid (BM25 + dense + RRF) machinery as user memory,
but over a separate index/collection with its own ``kb_version``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef
from hermes_cgm_agent.services.memory.retrieval import (
    Embedder,
    HashingEmbedder,
    HybridRetriever,
    MemoryDoc,
)

DEFAULT_KB_PATH = Path(__file__).resolve().parents[4] / "knowledge" / "authoritative_kb.json"


@dataclass(frozen=True)
class AuthoritativeDocument:
    doc_id: str
    title: str
    text: str
    tags: list[str] = field(default_factory=list)
    source: str | None = None


@dataclass(frozen=True)
class KnowledgeBase:
    kb_version: str
    documents: list[AuthoritativeDocument]


def load_knowledge_base(path: str | Path | None = None) -> KnowledgeBase:
    kb_path = Path(path) if path else DEFAULT_KB_PATH
    data = json.loads(kb_path.read_text(encoding="utf-8"))
    docs = [
        AuthoritativeDocument(
            doc_id=d["doc_id"],
            title=d["title"],
            text=d["text"],
            tags=list(d.get("tags", [])),
            source=d.get("source"),
        )
        for d in data.get("documents", [])
    ]
    return KnowledgeBase(kb_version=data["kb_version"], documents=docs)


class AuthoritativeRAGService:
    def __init__(
        self,
        *,
        knowledge_base: KnowledgeBase | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base or load_knowledge_base()
        self.retriever = HybridRetriever(embedder=embedder or HashingEmbedder())
        # index documents as the title + text so both exact terms and concepts hit
        self._docs = [
            MemoryDoc(doc_id=d.doc_id, text=f"{d.title}. {d.text}", layer="authoritative_kb")
            for d in self.knowledge_base.documents
        ]
        self._by_id = {d.doc_id: d for d in self.knowledge_base.documents}

    @property
    def kb_version(self) -> str:
        return self.knowledge_base.kb_version

    def search(self, query: str, *, top_k: int = 3) -> list[dict]:
        results = self.retriever.retrieve(query, self._docs, top_k=top_k)
        out: list[dict] = []
        for r in results:
            doc = self._by_id[r.doc.doc_id]
            out.append(
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "text": doc.text,
                    "source": doc.source,
                    "kb_version": self.kb_version,
                    "score": round(r.score, 6),
                    "evidence_ref": EvidenceRef(
                        kind="authoritative_kb",
                        ref_id=f"{self.kb_version}:{doc.doc_id}",
                        summary=doc.title,
                    ).model_dump(mode="json"),
                }
            )
        return out
