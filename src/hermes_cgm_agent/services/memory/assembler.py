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
import re
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
from hermes_cgm_agent.services.safety.memory_guard import (
    assert_track_isolation,
    resolve_conflict,
)

if TYPE_CHECKING:
    # Imported lazily at call time to avoid a rag <-> memory circular import
    # (rag.authoritative imports memory.retrieval, which pulls memory/__init__).
    from hermes_cgm_agent.services.rag.authoritative import AuthoritativeRAGService

# B6: maximum number of hot (L2/L3) items injected into memory context.
# Beyond this, items are truncated by ``updated_at`` descending so the most
# recently verified beliefs survive.
_MAX_HOT_ITEMS = 50


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
        hot_items: list[dict] = []
        for profile in self.repository.list_profile_items(user_id):
            summary = _profile_summary(profile)
            hot_items.append(
                {
                    "summary": summary,
                    "layer": "L2",
                    "score": 1.0,
                    "matched": True,
                    "hot": True,
                    "updated_at": profile.updated_at.isoformat(),  # B6: truncation key
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
        from hermes_cgm_agent.services.reports.narrative_templates import render_hypothesis_narrative
        for hyp in active_hypotheses:
            summary = render_hypothesis_narrative(hyp.state, hyp.statement, hyp.evidence_count)
            hot_items.append(
                {
                    "summary": summary,
                    "layer": "L3",
                    "score": 1.0,
                    "matched": True,
                    "hot": True,
                    "updated_at": hyp.updated_at.isoformat(),  # B6: truncation key
                    "evidence_refs": [
                        EvidenceRef(
                            kind="user_memory", ref_id=hyp.hypothesis_id, summary=summary
                        ).model_dump(mode="json")
                    ],
                }
            )

        # B6: cap hot items to prevent unbounded context growth. When active
        # L2/L3 entries exceed _MAX_HOT_ITEMS, keep the most recently updated.
        if len(hot_items) > _MAX_HOT_ITEMS:
            hot_items = sorted(
                hot_items,
                key=lambda x: x.get("updated_at", ""),
                reverse=True,
            )[:_MAX_HOT_ITEMS]
        items.extend(hot_items)

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
        # B6: auto-enforce dual-track isolation before returning so new call
        # paths can never bypass the guard (M6).
        assert_track_isolation(memory_items=items, authoritative_documents=None)
        return MemoryContext(enabled=True, items=items)

    def build_authoritative_context(
        self,
        *,
        query: str,
        top_k: int = 3,
        population: str | None = None,
    ) -> AuthoritativeContext:
        if self.rag_service is None:
            from hermes_cgm_agent.services.rag.authoritative import (
                AuthoritativeRAGService,
            )

            self.rag_service = AuthoritativeRAGService()
        results = self.rag_service.search(query, top_k=top_k, population=population)
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
        # B6: auto-enforce dual-track isolation before returning so new call
        # paths can never bypass the guard (M6).
        assert_track_isolation(memory_items=None, authoritative_documents=documents)
        return AuthoritativeContext(enabled=True, documents=documents)


# ── D031 numeric-conflict detection ─────────────────────────────────────────
# When a personal belief carries an explicit glucose range that does not even
# overlap the authoritative KB range, the two are in semantic contradiction and
# resolve_conflict must arbitrate (authoritative wins, gentle presentation).
# Detection is deliberately lexical/numeric only — no embeddings — to keep the
# false-positive rate near zero; textual contradictions stay out of scope.

_RANGE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-–~至到]\s*(\d+(?:\.\d+)?)\s*(mg/dl|mmol/l)?",
    re.IGNORECASE,
)
_GLUCOSE_KEYWORDS = ("血糖", "glucose", "tir", "目标范围", "target range")
_TARGET_RANGE_KEYWORDS = ("目标范围", "目标区间", "target range", "target glucose")
_MMOL_TO_MGDL = 18.016
# Plausible glucose bounds in mg/dL — ranges outside are ignored as noise
# (e.g. "3-5 次运动" would otherwise parse as a range).
_GLUCOSE_MIN_MGDL = 30.0
_GLUCOSE_MAX_MGDL = 600.0


def _range_clause(text: str, start: int, end: int) -> str:
    """Return the punctuation-delimited clause surrounding one range."""
    left = max(text.rfind(mark, 0, start) for mark in ("。", ".", ";", "；", "\n"))
    right_candidates = [
        pos
        for mark in ("。", ".", ";", "；", "\n")
        if (pos := text.find(mark, end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right].lower()


def _extract_glucose_ranges(
    text: str,
    *,
    require_target_context: bool = False,
) -> list[tuple[float, float]]:
    """Extract glucose ranges from text, normalized to mg/dL.

    A range is accepted only when a unit is attached or the text mentions
    glucose; unitless values <= 35 are assumed mmol/L (glucose in mg/dL is
    never that low while mmol/L never exceeds ~33).
    """
    if not text:
        return []
    ranges: list[tuple[float, float]] = []
    for match in _RANGE_PATTERN.finditer(text):
        low, high = float(match.group(1)), float(match.group(2))
        unit = (match.group(3) or "").lower()
        clause = _range_clause(text, match.start(), match.end())
        if require_target_context and not any(
            keyword in clause for keyword in _TARGET_RANGE_KEYWORDS
        ):
            continue
        if not unit and not any(keyword in clause for keyword in _GLUCOSE_KEYWORDS):
            continue
        if low > high:
            continue
        if unit == "mmol/l" or (not unit and high <= 35):
            low, high = low * _MMOL_TO_MGDL, high * _MMOL_TO_MGDL
        if low < _GLUCOSE_MIN_MGDL or high > _GLUCOSE_MAX_MGDL:
            continue
        ranges.append((low, high))
    return ranges


def detect_numeric_conflicts(
    memory_items: list[dict],
    authoritative_documents: list[dict],
) -> list[dict]:
    """Cross-check personal glucose ranges against authoritative KB ranges.

    Returns serialized ConflictResolution dicts (D031: authoritative always
    wins) for every personal/KB range pair with an empty intersection.
    """
    resolutions: list[dict] = []
    for doc in authoritative_documents or []:
        doc_text = f"{doc.get('title') or ''} {doc.get('text') or ''}"
        # A KB card can mention target, hypo, and hyper ranges together. Only
        # explicitly target-labelled ranges are comparable with a personal
        # "usual range"; threshold bands have different semantics and must not
        # create a spurious conflict resolution.
        doc_ranges = _extract_glucose_ranges(doc_text, require_target_context=True)
        if not doc_ranges:
            continue
        for item in memory_items or []:
            summary = str(item.get("summary") or "")
            for p_low, p_high in _extract_glucose_ranges(summary):
                overlaps = any(
                    p_low <= a_high and a_low <= p_high
                    for a_low, a_high in doc_ranges
                )
                if overlaps:
                    continue
                resolution = resolve_conflict(
                    authoritative={
                        "title": doc.get("title"),
                        "text": doc.get("text"),
                        "ranges_mgdl": [list(r) for r in doc_ranges],
                        "evidence_refs": doc.get("evidence_refs") or [],
                    },
                    personal={
                        "summary": summary,
                        "range_mgdl": [p_low, p_high],
                        "layer": item.get("layer"),
                        "evidence_refs": item.get("evidence_refs") or [],
                    },
                )
                resolutions.append(
                    {
                        "winner": resolution.winner,
                        "authoritative": resolution.authoritative,
                        "personal": resolution.personal,
                        "note": resolution.note,
                    }
                )
                break  # one resolution per (item, doc) pair is enough
    return resolutions


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
