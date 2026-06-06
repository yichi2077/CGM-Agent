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

    def test_query_number_coverage_mode_label(self) -> None:
        result = query_number_coverage(
            [{"claim_en": "TIR target >70%"}],
            "what about 99 percent",
        )
        self.assertEqual(result.mode, "coverage")
        self.assertTrue(result.violations)


if __name__ == "__main__":
    unittest.main()
