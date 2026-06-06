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
        for field in ("claim_en", "claim_zh", "text"):
            value = str(doc.get(field) or "")
            if value:
                supported_numbers.update(_significant_numbers(value))

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
    return {n for n in numbers if n not in {"1", "2", "3", "7", "14", "15", "30"}}
