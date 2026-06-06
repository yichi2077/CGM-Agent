"""Authoritative-knowledge RAG track — bilingual, citeable claim cards.

MEM-ARCH §6 / ADR-0001 §4 / DECISION_LOG D013 + D028 + D030.

This is the SECOND track of the dual-track RAG, physically separate from the
user-memory track. Evidence from here is tagged ``authoritative_kb`` and is
high-confidence / citeable; it MUST NOT be mixed with ``user_memory`` evidence.
On conflict, authoritative knowledge wins, but the caller should present it
gently as fact, not as a denial of the user (handled at generation time).

The unit is a **claim card** (D028): one atomic, bilingual, page-cited clinical
assertion — NOT a hand-written summary and NOT a naively chunked PDF page. Cards
carry a ``verified`` flag: until a clinician/reviewer signs off a safety-critical
card it is ``verified=false`` and the caller must present it as "pending
verification", never as settled authority.

Retrieval reuses the project's pure-Python hybrid machinery (BM25 + optional
semantic + RRF) over a separate in-memory index. Cards are bilingual (claim_zh +
claim_en) and the tokenizer indexes CJK bigrams, so a Chinese query recalls an
English-sourced card (D030). NOTE (ADR-0001 deviation): we index cards through
the existing BM25 retriever rather than a SQLite FTS5 table — same outcome
(lexical cross-lingual retrieval), less new surface, and it sidesteps FTS5's CJK
tokenization friction. The KB lives as packaged JSON + an in-memory index, never
in the mutable user DB, which reinforces its read-only/immutable separation from
personal memory (D031).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from hermes_cgm_agent.domain import EvidenceRef
from hermes_cgm_agent.services.memory.retrieval import (
    Embedder,
    HybridRetriever,
    MemoryDoc,
    build_authoritative_retriever,
    tokenize,
)

# C7: the KB ships as package data (hermes_cgm_agent/knowledge/) so it resolves
# both in an editable source tree and an installed wheel. Env var allows an
# operator override. The old repo-root fallback was brittle on install.
KB_ENV_VAR = "CGM_AGENT_KB_PATH"
KB_RESOURCE_PACKAGE = "hermes_cgm_agent.knowledge"
KB_RESOURCE_NAME = "authoritative_kb.json"

UNVERIFIED_MARKER_ZH = "待核验"
UNVERIFIED_MARKER_EN = "unverified"

# Retrieval guard (D041 correction). The authoritative retriever is sparse-only,
# and with embedder=None the RRF score degenerates to a rank function
# (1/(RRF_K+rank)) — it is NOT a relevance magnitude, so an absolute score floor
# would be meaningless here. The guard is therefore rank/lexical based:
#   1. trusted-first ordering — a verified card or a hand-authored ``curated``
#      card always ranks above machine-ingested ``auto`` draft cards, so an
#      unreviewed card can never crowd a curated card out of the top-k. This is
#      the fix for the measured dilution (seed hit@3 100% -> 84.4% after a noisy
#      auto-merge).
#   2. an untrusted (auto) card must share at least KB_MIN_UNTRUSTED_OVERLAP
#      tokenized query terms with its index text to be eligible, so weakly
#      matching draft fragments are dropped, not surfaced as "background clues".
TRUSTED_TIER = "curated"
AUTO_TIER = "auto"
KB_MIN_OVERLAP_ENV = "CGM_AGENT_KB_MIN_UNTRUSTED_OVERLAP"
DEFAULT_MIN_UNTRUSTED_OVERLAP = 1
# Retrieve a deeper candidate pool than top_k so a curated card sitting at a
# lower BM25 rank can still be promoted above auto cards by trusted-first.
KB_POOL_FACTOR = 8
KB_POOL_MIN = 25


def _is_trusted(card: "ClaimCard") -> bool:
    return bool(card.verified) or card.tier == TRUSTED_TIER


def _min_untrusted_overlap() -> int:
    raw = os.environ.get(KB_MIN_OVERLAP_ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_UNTRUSTED_OVERLAP
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MIN_UNTRUSTED_OVERLAP


def _resolve_kb_text(path: str | Path | None) -> str:
    if path is not None:
        return Path(path).read_text(encoding="utf-8")
    env_path = os.environ.get(KB_ENV_VAR)
    if env_path:
        return Path(env_path).read_text(encoding="utf-8")
    try:
        resource = resources.files(KB_RESOURCE_PACKAGE).joinpath(KB_RESOURCE_NAME)
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    raise FileNotFoundError(
        "authoritative_kb.json not found. Install the hermes_cgm_agent.knowledge "
        f"package data or set {KB_ENV_VAR} to a knowledge-base JSON file."
    )


@dataclass(frozen=True)
class ClaimCard:
    """One atomic, bilingual, page-cited clinical assertion (D028)."""

    card_id: str
    title: str
    claim_zh: str
    claim_en: str
    population: str = "general"
    tags: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    # Provenance tier (D041 correction): "curated" = hand-authored seed card;
    # "auto" = machine-ingested draft (always verified=false until a human signs
    # off). Retrieval prefers trusted (verified or curated) cards over auto cards
    # so unreviewed drafts cannot crowd out curated ones. Defaults to "curated"
    # for backward compatibility with pre-tier seed cards.
    tier: str = TRUSTED_TIER
    # Clinical sign-off provenance (P3b). A card may only be verified=true if it
    # records WHO/WHEN signed off — enforced by the KB validator, never auto-set.
    reviewer: str | None = None
    reviewed_at: str | None = None

    @property
    def citation(self) -> str:
        """Human-readable citation, e.g. 'Battelino 2019 Diabetes Care 42:1593-1603, p.16'."""
        cit = str(self.source.get("citation") or self.source.get("doc") or "").strip()
        page = self.source.get("page")
        if page is not None and str(page) not in cit:
            cit = f"{cit}, p.{page}" if cit else f"p.{page}"
        return cit

    @property
    def index_text(self) -> str:
        """Combined searchable text — both languages + title + tags + population."""
        return " ".join(
            [
                self.title,
                self.claim_zh,
                self.claim_en,
                self.population,
                " ".join(self.tags),
                " ".join(self.synonyms),
            ]
        )

    @property
    def verbatim(self) -> str:
        """The verbatim claim, both languages, for citation in generation."""
        return f"{self.claim_zh}\n{self.claim_en}"


@dataclass(frozen=True)
class KnowledgeBase:
    kb_version: str
    cards: list[ClaimCard]


def _card_from_dict(d: dict[str, Any]) -> ClaimCard:
    # Backward-tolerant: accept the legacy {title,text,tags,source(str)} shape so
    # an operator-provided custom KB still loads, mapping text -> both languages.
    if "claim_zh" not in d and "claim_en" not in d and "text" in d:
        text = str(d.get("text", ""))
        source = d.get("source")
        return ClaimCard(
            card_id=d["doc_id"] if "doc_id" in d else d["card_id"],
            title=str(d.get("title", "")),
            claim_zh=text,
            claim_en=text,
            population=str(d.get("population", "general")),
            tags=list(d.get("tags", [])),
            synonyms=list(d.get("synonyms", [])),
            source={"citation": source} if isinstance(source, str) else (source or {}),
            verified=bool(d.get("verified", False)),
            tier=str(d.get("tier", TRUSTED_TIER)),
        )
    return ClaimCard(
        card_id=d["card_id"],
        title=str(d.get("title", d["card_id"])),
        claim_zh=str(d.get("claim_zh", "")),
        claim_en=str(d.get("claim_en", "")),
        population=str(d.get("population", "general")),
        tags=list(d.get("tags", [])),
        synonyms=list(d.get("synonyms", [])),
        source=dict(d.get("source", {})),
        verified=bool(d.get("verified", False)),
        tier=str(d.get("tier", TRUSTED_TIER)),
        reviewer=d.get("reviewer"),
        reviewed_at=d.get("reviewed_at"),
    )


def load_knowledge_base(path: str | Path | None = None) -> KnowledgeBase:
    data = json.loads(_resolve_kb_text(path))
    raw = data.get("cards", data.get("documents", []))
    cards = [_card_from_dict(d) for d in raw]
    return KnowledgeBase(kb_version=data["kb_version"], cards=cards)


class AuthoritativeRAGService:
    def __init__(
        self,
        *,
        knowledge_base: KnowledgeBase | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base or load_knowledge_base()
        # D036: the authoritative KB is a small curated claim-card corpus. The
        # default is sparse-only BM25; tests may still inject an embedder.
        self.retriever = (
            HybridRetriever(embedder=embedder, reranker=None)
            if embedder is not None
            else build_authoritative_retriever()
        )
        self._docs = [
            MemoryDoc(doc_id=c.card_id, text=c.index_text, layer="authoritative_kb")
            for c in self.knowledge_base.cards
        ]
        self._by_id = {c.card_id: c for c in self.knowledge_base.cards}

    @property
    def kb_version(self) -> str:
        return self.knowledge_base.kb_version

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        population: str | None = None,
    ) -> list[dict]:
        docs = self._filter_docs(population)
        # Pull a deeper pool so the trusted-first guard can promote a curated card
        # that BM25 ranked below noisier auto cards (D041 correction).
        pool_k = max(top_k * KB_POOL_FACTOR, KB_POOL_MIN)
        results = self.retriever.retrieve(query, docs, top_k=pool_k)
        ranked = self._guarded_rank(results, q_terms=set(tokenize(query)))[:top_k]
        out: list[dict] = []
        for r in ranked:
            card = self._by_id[r.doc.doc_id]
            # Unverified cards are surfaced but clearly marked so the generation
            # layer never presents them as settled authority (D028 safety gate).
            ref_summary = card.title
            if not card.verified:
                ref_summary = f"{card.title} [{UNVERIFIED_MARKER_ZH}/{UNVERIFIED_MARKER_EN}]"
            out.append(
                {
                    "doc_id": card.card_id,
                    "title": card.title,
                    "text": card.verbatim,
                    "claim_zh": card.claim_zh,
                    "claim_en": card.claim_en,
                    "population": card.population,
                    "source": card.citation,
                    "citation": dict(card.source),
                    "verified": card.verified,
                    "tier": card.tier,
                    "quote_instruction": "verbatim_only",
                    "kb_version": self.kb_version,
                    "score": round(r.score, 6),
                    "evidence_ref": EvidenceRef(
                        kind="authoritative_kb",
                        ref_id=f"{self.kb_version}:{card.card_id}",
                        summary=ref_summary,
                    ).model_dump(mode="json"),
                }
            )
        return out

    def _guarded_rank(self, results: list, q_terms: set[str]) -> list:
        """Trusted-first ordering + an overlap gate for untrusted auto cards.

        ``results`` arrive in score-descending order; partitioning into two lists
        preserves that order within each group, so trusted (verified/curated)
        cards keep their relative ranking and always precede auto draft cards.
        """
        min_overlap = _min_untrusted_overlap()
        trusted: list = []
        untrusted: list = []
        for r in results:
            card = self._by_id[r.doc.doc_id]
            if _is_trusted(card):
                trusted.append(r)
                continue
            if len(q_terms & set(tokenize(r.doc.text))) >= min_overlap:
                untrusted.append(r)
        return trusted + untrusted

    def _filter_docs(self, population: str | None) -> list[MemoryDoc]:
        if not population:
            return self._docs
        normalized = population.strip().lower()
        filtered = [
            doc
            for doc in self._docs
            if self._by_id[doc.doc_id].population.lower() in {normalized, "general"}
        ]
        return filtered or self._docs
