from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


KEYWORD_GROUPS: dict[str, list[str]] = {
    "tir": ["time in range", "tir", "目标范围", "range"],
    "hypoglycemia": ["hypoglycemia", "low glucose", "低血糖", "15-15"],
    "agp": ["ambulatory glucose profile", "agp", "standardized report"],
    "variability": ["coefficient of variation", "%cv", "variability", "变异"],
}


@dataclass(frozen=True)
class CandidateCard:
    card_id: str
    title: str
    claim_zh: str
    claim_en: str
    population: str = "general"
    tags: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_status: str = "candidate"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_claim_card_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("review_status", None)
        return payload


@dataclass(frozen=True)
class IngestResult:
    source_path: str
    page_count: int
    candidate_count: int
    candidates: list[CandidateCard]


def extract_pdf_text(path: str | Path) -> list[tuple[int, str]]:
    """Extract per-page text with optional local PDF libraries.

    This helper deliberately has no hard dependency: production operators may
    install pdfplumber/pypdf in the ingest environment, while the runtime agent
    remains lightweight.
    """

    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(pdf_path)) as pdf:
            return [
                (index + 1, page.extract_text() or "")
                for index, page in enumerate(pdf.pages)
            ]
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        return [
            (index + 1, page.extract_text() or "")
            for index, page in enumerate(reader.pages)
        ]
    except Exception as exc:
        raise RuntimeError(
            "PDF text extraction requires pdfplumber or pypdf in the ingest environment."
        ) from exc


def build_candidate_cards(
    *,
    source_path: str | Path,
    pages: list[tuple[int, str]],
    kb_version: str,
    max_cards_per_group: int = 8,
) -> IngestResult:
    source = Path(source_path)
    candidates: list[CandidateCard] = []
    counts_by_group: dict[str, int] = {group: 0 for group in KEYWORD_GROUPS}
    for page_number, text in pages:
        normalized = _normalize_text(text)
        if not normalized:
            continue
        lowered = normalized.lower()
        for group, keywords in KEYWORD_GROUPS.items():
            if counts_by_group[group] >= max_cards_per_group:
                continue
            if not any(keyword.lower() in lowered for keyword in keywords):
                continue
            counts_by_group[group] += 1
            sentence = _best_sentence(normalized, keywords)
            card_id = _candidate_id(source.stem, group, page_number, counts_by_group[group])
            candidates.append(
                CandidateCard(
                    card_id=card_id,
                    title=f"{group.upper()} candidate from {source.stem} p.{page_number}",
                    claim_zh="待人工翻译/核验: " + sentence,
                    claim_en=sentence,
                    population="general",
                    tags=[group, *keywords[:3]],
                    synonyms=keywords,
                    source={
                        "doc": source.name,
                        "citation": source.stem,
                        "page": page_number,
                        "section": "candidate extraction",
                        "kb_version": kb_version,
                    },
                    verified=False,
                )
            )
    return IngestResult(
        source_path=str(source),
        page_count=len(pages),
        candidate_count=len(candidates),
        candidates=candidates,
    )


def write_candidate_json(result: IngestResult, path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_path": result.source_path,
        "page_count": result.page_count,
        "candidate_count": result.candidate_count,
        "cards": [card.to_claim_card_dict() for card in result.candidates],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_review_markdown(result: IngestResult, path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# KB Candidate Review",
        "",
        f"- Source: `{result.source_path}`",
        f"- Pages parsed: {result.page_count}",
        f"- Candidate cards: {result.candidate_count}",
        "",
        "Review rules:",
        "",
        "- Do not mark a card verified until the source page and wording are manually checked.",
        "- Fill `reviewer` or `reviewed_at` before moving a card into production KB.",
        "- Keep one atomic claim per card.",
        "",
    ]
    for card in result.candidates:
        lines.extend(
            [
                f"## {card.card_id}",
                "",
                f"- Title: {card.title}",
                f"- Page: {card.source.get('page')}",
                f"- Tags: {', '.join(card.tags)}",
                "",
                "Claim EN:",
                "",
                card.claim_en,
                "",
                "Claim ZH:",
                "",
                card.claim_zh,
                "",
                "Reviewer notes:",
                "",
                "- [ ] Source page checked",
                "- [ ] Numbers and units checked",
                "- [ ] Population checked",
                "- [ ] Translation checked",
                "- [ ] Ready for `verified=true`",
                "",
            ]
        )
    out.write_text("\n".join(lines), encoding="utf-8")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _best_sentence(text: str, keywords: list[str]) -> str:
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in lowered_keywords):
            return sentence.strip()[:800]
    return text[:800]


def _candidate_id(stem: str, group: str, page_number: int, index: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", stem.lower()).strip("-")
    return f"cand-{safe}-{group}-p{page_number}-{index}"


SENTENCE_KEYWORDS = (
    "time in range",
    "tir",
    "tbr",
    "tar",
    "hypoglycemia",
    "hyperglycemia",
    "glucose",
    "mg/dl",
    "mmol",
    "target",
    "cgm",
    "a1c",
    "gmi",
    "variability",
    "低血糖",
    "高血糖",
    "目标",
    "范围",
)


def build_sentence_candidates(
    *,
    source_path: str | Path,
    pages: list[tuple[int, str]],
    kb_version: str,
    citation: str,
    doc_title: str,
    population: str = "general",
    max_cards_per_page: int = 6,
) -> IngestResult:
    """Deterministic sentence-level extractor for offline KB expansion."""
    source = Path(source_path)
    candidates: list[CandidateCard] = []
    for page_number, text in pages:
        normalized = _normalize_text(text)
        if not normalized:
            continue
        sentences = re.split(r"(?<=[.!?。！？])\s+", normalized)
        page_count = 0
        for index, sentence in enumerate(sentences, start=1):
            if page_count >= max_cards_per_page:
                break
            cleaned = sentence.strip()
            if len(cleaned) < 40 or len(cleaned) > 900:
                continue
            lowered = cleaned.lower()
            if not any(keyword in lowered for keyword in SENTENCE_KEYWORDS):
                continue
            if not re.search(r"\d", cleaned):
                continue
            if any(token in lowered for token in ("references", "copyright", "correspondence")):
                continue
            page_count += 1
            card_id = f"auto-{source.stem}-p{page_number}-s{index}"
            candidates.append(
                CandidateCard(
                    card_id=card_id,
                    title=f"{source.stem} p.{page_number} sentence {index}",
                    claim_zh="待人工翻译/核验: " + cleaned,
                    claim_en=cleaned,
                    population=population,
                    tags=["auto-sentence"],
                    synonyms=[],
                    source={
                        "doc": doc_title,
                        "citation": citation,
                        "page": page_number,
                        "section": "sentence extraction",
                        "kb_version": kb_version,
                    },
                    verified=False,
                    metadata={"extraction_mode": "text-heuristic"},
                )
            )
    return IngestResult(
        source_path=str(source),
        page_count=len(pages),
        candidate_count=len(candidates),
        candidates=candidates,
    )
