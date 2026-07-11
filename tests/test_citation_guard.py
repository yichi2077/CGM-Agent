from __future__ import annotations

import unittest

from hermes_cgm_agent.services.safety.citation_guard import (
    assert_authoritative_quotes,
    query_number_coverage,
)


class CitationGuardTests(unittest.TestCase):
    def test_warn_mode_allows_unmapped_number(self) -> None:
        result = assert_authoritative_quotes(
            [{"claim_en": "TIR target >70%", "claim_zh": "TIR >70%"}],
            "The guideline says 99 percent.",
            strict=False,
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.violations)

    def test_strict_mode_blocks_unmapped_number(self) -> None:
        result = assert_authoritative_quotes(
            [{"claim_en": "TIR target >70%", "claim_zh": "TIR >70%"}],
            "The guideline says 99 percent.",
            strict=True,
        )
        self.assertFalse(result.ok)

    def test_exact_number_match_passes(self) -> None:
        # A number present verbatim in a card is supported (no violation).
        result = assert_authoritative_quotes(
            [{"claim_en": "Target time in range is above 70 percent."}],
            "Guidelines put the time-in-range target at 70 percent.",
            strict=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_substring_is_not_a_match(self) -> None:
        # Regression: "5" must NOT be considered supported just because it is a
        # substring of an unrelated "2025" in a card (the old substring bug).
        result = assert_authoritative_quotes(
            [{"claim_en": "Updated guidance published in 2025."}],
            "Aim for 5 mmol per litre.",
            strict=True,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("5" in v for v in result.violations))

    def test_empty_text_passes_in_strict(self) -> None:
        # F3-T005(d): whitespace-only generated text has nothing to back → ok.
        result = assert_authoritative_quotes(
            [{"claim_en": "TIR target >70%"}], "   ", strict=True
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_no_cards_means_every_number_unbacked_in_strict(self) -> None:
        # F3-T005(e): with no backing cards (e.g. no verified KB hit), every
        # significant number in the narrative is unbacked → blocked in strict.
        result = assert_authoritative_quotes(
            [], "Aim to keep time-in-range above 70 percent.", strict=True
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.violations)

    def test_mixed_backed_and_unbacked_blocks_in_strict(self) -> None:
        # F3-T005(f): one backed (70) + one unbacked (88) → still blocked.
        result = assert_authoritative_quotes(
            [{"claim_en": "Target time in range above 70 percent."}],
            "Keep time-in-range above 70 percent, not 88 percent.",
            strict=True,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("88" in v for v in result.violations))
        self.assertFalse(any("70" in v for v in result.violations))

    def test_guard_runs_on_output_not_input(self) -> None:
        # F3-T005: prompt-injection resilience — a malicious number in the user
        # query never matters; the guard only inspects the generated narrative.
        result = assert_authoritative_quotes(
            [{"claim_en": "Target time in range above 70 percent."}],
            "Keep time-in-range above 70 percent.",  # generated text is clean
            strict=True,
        )
        self.assertTrue(result.ok)

    def test_query_number_coverage_mode_label(self) -> None:
        result = query_number_coverage(
            [{"claim_en": "TIR target >70%"}],
            "what about 99 percent",
        )
        self.assertEqual(result.mode, "coverage")
        self.assertTrue(result.violations)

    # --- C1: title / source.citation field coverage (audit item P4) -----------
    # Previously the guard only mined claim_en/claim_zh/text for backing numbers,
    # so a number that appeared solely in a card's title or bibliographic
    # citation was wrongly reported as "lacking authoritative evidence". These
    # tests pin the expanded extraction.

    def test_title_number_supports_narrative(self) -> None:
        # C1: a guideline id like "1593" that lives only in the card title must
        # back the same number in the generated narrative (claim body has no 1593).
        result = assert_authoritative_quotes(
            [{"title": "ADA Standards 1593: TIR target", "claim_en": "Time in range target"}],
            "Guideline 1593 sets the time-in-range target.",
            strict=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_source_citation_string_number_supports_narrative(self) -> None:
        # C1: the RAG search result exposes ``source`` as a human-readable
        # citation STRING (e.g. "Diabetes Care 42:1593"). A volume/page digit
        # like "42" quoted in the narrative must be backed by it.
        result = assert_authoritative_quotes(
            [{"claim_en": "Carb counting guidance", "source": "Diabetes Care 42:1593-1603, p.16"}],
            "See Diabetes Care volume 42 for the carb-counting detail.",
            strict=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_source_citation_dict_number_supports_narrative(self) -> None:
        # C1: ClaimCard-style dicts store ``source`` as a nested dict with a
        # ``citation`` key. A page/reference digit like "42" appearing only there
        # must back the narrative.
        result = assert_authoritative_quotes(
            [{"claim_en": "Carb counting guidance", "source": {"citation": "ADA Standards p.42", "page": 42}}],
            "The detail is on page 42 of the standard.",
            strict=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_15_15_rule_in_title_is_backed(self) -> None:
        # C1: the "15-15" hypoglycaemia rule. "15" is deliberately NOT exempt
        # (it is the core number of hypo-treatment advice), so it MUST be backed
        # by a card. Here "15" appears only in the title — before C1 this was
        # blocked as a hallucination; now it is correctly recognized as sourced.
        result = assert_authoritative_quotes(
            [{"title": "15-15 法则（低血糖处理）", "claim_en": "Treat lows with fast carbs"}],
            "遵循 15-15 法则：摄入 15 克快速碳水。",
            strict=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_unrelated_number_still_blocked_with_title_and_source(self) -> None:
        # C1 regression guard: expanding extraction to title/source must NOT
        # over-broaden. A number absent from every card field is still flagged.
        result = assert_authoritative_quotes(
            [
                {
                    "title": "ADA Standards 1593",
                    "claim_en": "TIR target above 70 percent.",
                    "source": "Diabetes Care 42:1593-1603, p.16",
                }
            ],
            "The recommendation is 88 percent.",
            strict=True,
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("88" in v for v in result.violations))
        # 70 (claim_en), 1593/42/16 (title/source) are present in the card but
        # not referenced by the narrative — they must NOT appear as violations.
        self.assertFalse(any("70" in v for v in result.violations))
        self.assertFalse(any("1593" in v for v in result.violations))
        self.assertFalse(any("42" in v for v in result.violations))


if __name__ == "__main__":
    unittest.main()
