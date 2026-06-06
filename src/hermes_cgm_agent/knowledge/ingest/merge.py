from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_cgm_agent.services.rag.authoritative import ClaimCard, load_knowledge_base

DEFAULT_KB_PATH = Path(__file__).resolve().parents[1] / "authoritative_kb.json"


@dataclass(frozen=True)
class MergePreview:
    added: list[str]
    skipped: list[str]
    total_after: int
    kb_version: str


def load_candidates_file(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cards = payload.get("cards", payload.get("candidates", []))
    if not isinstance(cards, list):
        raise ValueError("candidate file must contain a cards array")
    return cards


def merge_candidates_into_kb(
    *,
    candidates_path: str | Path,
    kb_path: str | Path | None = None,
    dry_run: bool = False,
    kb_version: str | None = None,
) -> MergePreview:
    candidate_cards = load_candidates_file(candidates_path)
    kb = load_knowledge_base(kb_path)
    existing_by_id = {card.card_id: card for card in kb.cards}
    added: list[str] = []
    skipped: list[str] = []
    merged_cards = [_card_to_dict(card) for card in kb.cards]

    for raw in candidate_cards:
        card_id = str(raw.get("card_id") or "")
        if not card_id:
            skipped.append("<missing-id>")
            continue
        if card_id in existing_by_id:
            skipped.append(card_id)
            continue
        normalized = dict(raw)
        normalized["verified"] = False
        normalized.pop("reviewer", None)
        normalized.pop("reviewed_at", None)
        merged_cards.append(_card_dict_from_candidate(normalized))
        existing_by_id[card_id] = None  # type: ignore[assignment]
        added.append(card_id)

    target_version = kb_version or kb.kb_version
    preview = MergePreview(
        added=added,
        skipped=skipped,
        total_after=len(merged_cards),
        kb_version=target_version,
    )
    if dry_run:
        return preview

    target = _resolve_kb_path(kb_path)
    payload = {
        "kb_version": target_version,
        "schema": "claim-cards-v1",
        "source_note": (
            "Bilingual claim cards merged via kb-merge. Auto-ingested cards remain "
            "verified=false pending external review."
        ),
        "cards": merged_cards,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return preview


def _resolve_kb_path(kb_path: str | Path | None) -> Path:
    if kb_path is not None:
        return Path(kb_path)
    return DEFAULT_KB_PATH


def _card_to_dict(card: ClaimCard) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "card_id": card.card_id,
        "title": card.title,
        "claim_zh": card.claim_zh,
        "claim_en": card.claim_en,
        "population": card.population,
        "tags": card.tags,
        "synonyms": card.synonyms,
        "source": card.source,
        "verified": card.verified,
        "tier": card.tier,
    }
    if card.reviewer:
        payload["reviewer"] = card.reviewer
    if card.reviewed_at:
        payload["reviewed_at"] = card.reviewed_at
    return payload


def _card_dict_from_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": raw["card_id"],
        "title": raw.get("title", raw["card_id"]),
        "claim_zh": raw.get("claim_zh", ""),
        "claim_en": raw.get("claim_en", ""),
        "population": raw.get("population", "general"),
        "tags": list(raw.get("tags") or []),
        "synonyms": list(raw.get("synonyms") or []),
        "source": dict(raw.get("source") or {}),
        "verified": False,
        # Machine-ingested cards are always the untrusted "auto" tier until a
        # human signs off; retrieval ranks them below curated cards (D041).
        "tier": "auto",
    }
