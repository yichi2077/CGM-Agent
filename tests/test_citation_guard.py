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


if __name__ == "__main__":
    unittest.main()
