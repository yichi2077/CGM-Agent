"""ClaimCard / knowledge-base validator (P3b scaffolding, ADR-0001 §4 / D028).

This validates **structure and sign-off provenance only**. It NEVER flips a
card's ``verified`` flag — clinical verification is a human step (P3b). Its job:

1. keep the KB well-formed (required fields, types, citation/page shape, unique ids);
2. enforce the safety gate that a ``verified=true`` card MUST carry reviewer
   provenance (`reviewer` or `reviewed_at`), so no card can silently claim
   authority without a recorded sign-off.

Use it as a CI/ops gate (CLI ``kb-validate``) before shipping a KB version.
"""

from __future__ import annotations

from hermes_cgm_agent.services.rag.authoritative import (
    ClaimCard,
    KnowledgeBase,
    load_knowledge_base,
)

REQUIRED_TEXT_FIELDS = ("card_id", "title", "claim_zh", "claim_en")


class KnowledgeBaseValidationError(ValueError):
    """Raised when a knowledge base fails structural / sign-off validation."""


def validate_card(card: ClaimCard) -> list[str]:
    """Return a list of problems for one card (empty = valid)."""
    cid = card.card_id or "<no card_id>"
    problems: list[str] = []

    for field_name in REQUIRED_TEXT_FIELDS:
        if not str(getattr(card, field_name, "") or "").strip():
            problems.append(f"{cid}: missing/empty required field '{field_name}'")

    if not isinstance(card.verified, bool):
        problems.append(f"{cid}: 'verified' must be a boolean")
    if not isinstance(card.synonyms, list) or not all(
        isinstance(item, str) for item in card.synonyms
    ):
        problems.append(f"{cid}: 'synonyms' must be a list of strings")

    source = card.source or {}
    if not str(source.get("citation") or source.get("doc") or "").strip():
        problems.append(f"{cid}: source must include a non-empty 'citation' or 'doc'")
    page = source.get("page")
    if page is not None and not isinstance(page, int):
        problems.append(f"{cid}: source.page must be an integer or null")

    # Safety gate: a verified card must record who/when signed off (P3b).
    if card.verified and not (
        str(card.reviewer or "").strip() or str(card.reviewed_at or "").strip()
    ):
        problems.append(
            f"{cid}: verified=true requires 'reviewer' or 'reviewed_at' provenance"
        )

    return problems


def validate_knowledge_base(kb: KnowledgeBase | None = None) -> list[str]:
    """Return a list of problems for the whole KB (empty = valid)."""
    kb = kb or load_knowledge_base()
    problems: list[str] = []

    if not str(kb.kb_version or "").strip():
        problems.append("kb_version is empty")
    if not kb.cards:
        problems.append("knowledge base has no cards")

    seen: set[str] = set()
    for card in kb.cards:
        problems.extend(validate_card(card))
        if card.card_id in seen:
            problems.append(f"duplicate card_id: {card.card_id}")
        seen.add(card.card_id)

    return problems


def assert_valid_knowledge_base(kb: KnowledgeBase | None = None) -> None:
    problems = validate_knowledge_base(kb)
    if problems:
        raise KnowledgeBaseValidationError("; ".join(problems))
