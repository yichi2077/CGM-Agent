from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from hermes_cgm_agent.knowledge.ingest.pdf_loader import PageChunk
from hermes_cgm_agent.knowledge.ingest.pipeline import CandidateCard
from hermes_cgm_agent.services.rag.authoritative import ClaimCard
from hermes_cgm_agent.services.rag.validator import validate_card

NOISE_PATTERNS = (
    "author",
    "abbreviation",
    "references",
    "table of contents",
    "copyright",
    "correspondence",
)

THRESHOLD_TAGS = ("tir", "tbr", "tar", "hypoglycemia", "target", "threshold", "mg/dl", "mmol")
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")

# Bibliographic / masthead markers that signal a non-claim metadata line rather
# than a clinical assertion (defense-in-depth; the Hermes prompt already skips
# these, but the deterministic engine and any model slip-through must not enter
# the KB). Kept conservative to avoid rejecting genuine claims.
METADATA_MARKERS = (
    "doi:",
    "doi.org",
    "http://",
    "https://",
    "issn",
    "received:",
    "accepted:",
    "published online:",
    "all rights reserved",
    "©",
)
# Page-number-prefixed structural fragments, e.g. "1 TITLE: ...", "4 WORD COUNT".
TITLE_PREFIX_PATTERN = re.compile(
    r"^\s*\d+\s+(abstract|title|running title|word count|figures?|tables?|keywords?|references?)\b",
    re.IGNORECASE,
)
# Private Use Area ranges + the replacement char indicate broken PDF text
# extraction (e.g. CID-font Chinese PDFs rendering as ``􀆰``). Such text is
# unreadable and must never become a citeable card.
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


@dataclass(frozen=True)
class QualityDecision:
    card: CandidateCard
    accepted: bool
    reason: str
    extraction_mode: str = "text"


@dataclass
class QualityReport:
    accepted: list[CandidateCard] = field(default_factory=list)
    rejected: list[QualityDecision] = field(default_factory=list)
    decisions: list[QualityDecision] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def filter_candidates(
    candidates: list[CandidateCard],
    *,
    pages_by_no: dict[int, PageChunk] | None = None,
    existing_cards: list[ClaimCard] | None = None,
) -> QualityReport:
    report = QualityReport()
    seen_ids: set[str] = {card.card_id for card in existing_cards or []}
    seen_hashes: set[str] = {_claim_hash(card) for card in existing_cards or []}

    for candidate in candidates:
        page = (pages_by_no or {}).get(int(candidate.source.get("page") or 0))
        decision = _evaluate_candidate(
            candidate,
            page=page,
            seen_ids=seen_ids,
            seen_hashes=seen_hashes,
        )
        report.decisions.append(decision)
        if decision.accepted:
            report.accepted.append(candidate)
            seen_ids.add(candidate.card_id)
            seen_hashes.add(_claim_hash(candidate))
        else:
            report.rejected.append(decision)
    return report


def write_quality_markdown(report: QualityReport, path: str) -> None:
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text_counts = sum(1 for d in report.rejected if d.extraction_mode == "text")
    vision_counts = sum(1 for d in report.rejected if d.extraction_mode == "vision")
    lines = [
        "# KB Quality Report",
        "",
        f"- Accepted: {report.accepted_count}",
        f"- Rejected: {report.rejected_count}",
        f"- Rejected text-mode: {text_counts}",
        f"- Rejected vision-mode: {vision_counts}",
        "",
        "## Rejected",
        "",
    ]
    for decision in report.rejected:
        lines.extend(
            [
                f"### {decision.card.card_id}",
                "",
                f"- mode: {decision.extraction_mode}",
                f"- reason: {decision.reason}",
                "",
                decision.card.claim_en,
                "",
            ]
        )
    out.write_text("\n".join(lines), encoding="utf-8")


def _evaluate_candidate(
    candidate: CandidateCard,
    *,
    page: PageChunk | None,
    seen_ids: set[str],
    seen_hashes: set[str],
) -> QualityDecision:
    mode = str(candidate.metadata.get("extraction_mode") or (page.extraction_mode if page else "text"))
    claim_card = ClaimCard(
        card_id=candidate.card_id,
        title=candidate.title,
        claim_zh=candidate.claim_zh,
        claim_en=candidate.claim_en,
        population=candidate.population,
        tags=candidate.tags,
        synonyms=candidate.synonyms,
        source=candidate.source,
        verified=False,
    )
    problems = validate_card(claim_card)
    if problems:
        return QualityDecision(candidate, False, problems[0], extraction_mode=mode)

    claim_en = candidate.claim_en.strip()
    lowered = claim_en.lower()
    if len(claim_en) < 40 or len(claim_en) > 1200:
        return QualityDecision(candidate, False, "claim length out of bounds", extraction_mode=mode)
    if any(pattern in lowered for pattern in NOISE_PATTERNS):
        return QualityDecision(candidate, False, "noise pattern matched", extraction_mode=mode)
    if _has_mojibake(claim_en):
        return QualityDecision(candidate, False, "mojibake/unreadable glyphs", extraction_mode=mode)
    if any(marker in lowered for marker in METADATA_MARKERS):
        return QualityDecision(candidate, False, "bibliographic/metadata line", extraction_mode=mode)
    if TITLE_PREFIX_PATTERN.match(claim_en):
        return QualityDecision(candidate, False, "title/page-number fragment", extraction_mode=mode)
    if _looks_like_threshold_claim(candidate) and not NUMBER_PATTERN.search(claim_en):
        return QualityDecision(candidate, False, "threshold claim missing numeric value", extraction_mode=mode)
    if candidate.card_id in seen_ids or _claim_hash(candidate) in seen_hashes:
        return QualityDecision(candidate, False, "duplicate card", extraction_mode=mode)

    if mode in {"text-heuristic"}:
        if page and not _text_supported(candidate.claim_en, page.text):
            return QualityDecision(candidate, False, "heuristic claim not supported by source page text", extraction_mode=mode)
    elif mode == "vision":
        if str(candidate.metadata.get("source_evidence") or "") == "figure" and not NUMBER_PATTERN.search(claim_en):
            return QualityDecision(candidate, False, "figure claim without numeric evidence", extraction_mode=mode)
        if NUMBER_PATTERN.search(claim_en) and not _vision_numbers_verified(candidate, page):
            return QualityDecision(candidate, False, "vision numbers not cross-verified", extraction_mode=mode)
    else:
        if page and not _text_supported(candidate.claim_en, page.text):
            return QualityDecision(candidate, False, "claim not supported by source page text", extraction_mode=mode)

    return QualityDecision(candidate, True, "accepted", extraction_mode=mode)


def _has_mojibake(text: str) -> bool:
    if "�" in text:
        return True
    return any(
        any(lo <= ord(ch) <= hi for lo, hi in _PUA_RANGES)
        for ch in text
    )


def _looks_like_threshold_claim(candidate: CandidateCard) -> bool:
    haystack = " ".join([candidate.title, candidate.claim_en, *candidate.tags]).lower()
    return any(tag in haystack for tag in THRESHOLD_TAGS)


def _text_supported(claim_en: str, page_text: str) -> bool:
    claim = _normalize(claim_en)
    source = _normalize(page_text)
    if not claim or not source:
        return False
    if claim in source:
        return True
    numbers = NUMBER_PATTERN.findall(claim_en)
    if numbers and all(number in source for number in numbers):
        return True
    return _jaccard(claim.split(), source.split()) >= 0.12


def _vision_numbers_verified(candidate: CandidateCard, page: PageChunk | None) -> bool:
    numbers = NUMBER_PATTERN.findall(candidate.claim_en)
    if not numbers:
        return True
    corpus = " ".join(
        [
            candidate.claim_en,
            page.tables_md if page else "",
            page.text if page else "",
        ]
    )
    return all(number in corpus for number in numbers)


def _claim_hash(candidate: CandidateCard) -> str:
    payload = _normalize(candidate.claim_en)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _jaccard(left: list[str], right: list[str]) -> float:
    set_left = set(left)
    set_right = set(right)
    if not set_left or not set_right:
        return 0.0
    return len(set_left & set_right) / len(set_left | set_right)
