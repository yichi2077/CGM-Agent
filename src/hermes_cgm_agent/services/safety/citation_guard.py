"""Anti-hallucination citation guard (F3-B1 / Principle I).

The function default is ``strict=False`` (warn) — the ``rag.verify_quotes`` tool
and ``test_rag`` depend on that backward-compatible behaviour (analyze N1). Strict
enforcement is MANDATORY and NON-BYPASSABLE at exactly one place: the report
pipeline gate (``ReportService.generate`` → ``assert_authoritative_quotes(
documents, generated_text, strict=True)``), which blocks delivery of any
medical-claim narrative carrying an unbacked number. Never relax that call site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class CitationGuardResult:
    ok: bool
    violations: list[str]
    mode: str


def assert_authoritative_quotes(
    documents: list[dict],
    generated_text: str,
    *,
    strict: bool = False,
) -> CitationGuardResult:
    """Warn or fail when medical numbers in GENERATED text lack KB evidence.

    Intended use: run over the model's generated narrative (the hallucination
    surface), NOT over the user's query. Each significant numeric value in the
    text must match — as a whole numeric token — a number that appears in a
    retrieved authoritative card. Matching is exact-token (not substring), so a
    "70" in the text is supported by a card's "70" but NOT by an unrelated
    "1700" or "2025".
    """
    violations: list[str] = []
    if not generated_text.strip():
        return CitationGuardResult(ok=True, violations=[], mode="strict" if strict else "warn")

    text_numbers = _significant_numbers(generated_text)
    if not text_numbers:
        return CitationGuardResult(ok=True, violations=[], mode="strict" if strict else "warn")

    supported_numbers: set[str] = set()
    for doc in documents:
        supported_numbers.update(_extract_supporting_numbers(doc))

    for number in sorted(text_numbers):
        if number not in supported_numbers:
            violations.append(f"number {number} lacks authoritative evidence mapping")

    ok = not violations
    if violations and not strict:
        return CitationGuardResult(ok=True, violations=violations, mode="warn")
    return CitationGuardResult(ok=ok, violations=violations, mode="strict" if strict else "warn")


def query_number_coverage(documents: list[dict], query: str) -> CitationGuardResult:
    """Retrieval-coverage signal (NOT anti-hallucination).

    Reports which significant numbers in the user's QUERY are not present in the
    retrieved evidence — a hint that retrieval may have missed the relevant card.
    This is deliberately separate from ``assert_authoritative_quotes`` so the
    two concerns are not conflated. ``mode`` is always "coverage".
    """
    result = assert_authoritative_quotes(documents, query, strict=False)
    return CitationGuardResult(ok=result.ok, violations=result.violations, mode="coverage")


def _significant_numbers(text: str) -> set[str]:
    numbers = set(NUMBER_PATTERN.findall(text))
    # Exempt only small counting numbers and the product's own window sizes
    # (1/2/3 counts, 7/14-day windows). 15 and 30 are deliberately NOT exempt:
    # they are the core numbers of hypo-treatment advice ("15 g 碳水、等 15
    # 分钟" rule) and carb guidance — exactly the numbers a hallucinated
    # recommendation would carry, so they must be backed by a card.
    return {n for n in numbers if n not in {"1", "2", "3", "7", "14"}}


def _extract_supporting_numbers(doc: dict) -> set[str]:
    """Collect significant numbers from every citation-bearing field of a card.

    Covers the claim body (``claim_en``/``claim_zh``/``text``) plus the card
    ``title`` and bibliographic ``source.citation`` (work package C1 / audit
    item P4). A number that appears only in a card's heading or citation — a
    guideline id like "1593", a volume digit like "42", or the "15-15"
    hypoglycaemia rule in the title — must still count as having authoritative
    backing; otherwise the guard falsely flags a narrative that legitimately
    quotes those numbers as a hallucination.

    The ``source`` field is polymorphic across call sites: the RAG search result
    serializes the human-readable citation as a STRING under ``source`` (with the
    raw dict mirrored under ``citation``), while ClaimCard-style dicts store
    ``source`` as a nested dict whose ``citation``/``doc``/``page`` entries hold
    the bibliographic strings. Both shapes are handled (see
    ``_numbers_from_source_field``) so backing is never missed regardless of
    caller. The same regex and exemption list (``_significant_numbers``) apply,
    unchanged.

    Design tradeoff (accepted by C1): a coincidental match between a hallucinated
    number and a citation/page digit will no longer be blocked. This is judged
    preferable to the prior false-positive storm where legitimately quoted
    citation numbers were rejected, and remains bounded by the exemption list
    and exact-token (not substring) matching.
    """
    numbers: set[str] = set()
    # Claim body — existing behaviour, unchanged.
    for field in ("claim_en", "claim_zh", "text"):
        value = str(doc.get(field) or "")
        if value:
            numbers.update(_significant_numbers(value))
    # Card heading (C1): guideline ids and rule labels often live in the title
    # (e.g. "15-15 法则", "ADA Standards 1593").
    title = str(doc.get("title") or "")
    if title:
        numbers.update(_significant_numbers(title))
    # Bibliographic citation (C1): handle both the string and dict shapes.
    numbers.update(_numbers_from_source_field(doc.get("source")))
    # M-12: the top-level ``citation`` dict (AuthoritativeDocument.citation)
    # was previously dropped when building the document dict in
    # _apply_citation_gate.  Now that it is included, extract its numbers too
    # so guideline ids / page numbers in the citation metadata count as
    # backing evidence.
    numbers.update(_numbers_from_source_field(doc.get("citation")))
    return numbers


def _numbers_from_source_field(source: object) -> set[str]:
    """Extract significant numbers from a polymorphic ``source`` value.

    A string is treated as a human-readable citation (e.g.
    "Battelino 2019 Diabetes Care 42:1593-1603, p.16"); a dict is treated as a
    ClaimCard ``source`` mapping whose ``citation``/``doc``/``page`` entries
    hold the bibliographic strings — mirroring the ``ClaimCard.citation``
    property that combines ``citation``/``doc`` with the ``page`` suffix.
    """
    numbers: set[str] = set()
    if isinstance(source, str):
        if source:
            numbers.update(_significant_numbers(source))
    elif isinstance(source, dict):
        for key in ("citation", "doc", "page"):
            value = str(source.get(key) or "")
            if value:
                numbers.update(_significant_numbers(value))
    return numbers
