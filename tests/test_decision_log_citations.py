from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
DECISION_LOG = PROJECT_ROOT / "docs" / "DECISION_LOG.md"

_D_PATTERN = re.compile(r"D0\d\d")


class DecisionLogCitationTests(unittest.TestCase):
    """AGENTS.md rule: every ``Dxxx`` decision citation in code MUST resolve to a
    DECISION_LOG entry — no phantom docs. This guard fails loudly on drift so a
    new citation can never silently dangle (it caught a real D042-vs-D043 slip)."""

    def test_all_code_decision_citations_resolve(self) -> None:
        defined = set(_D_PATTERN.findall(DECISION_LOG.read_text(encoding="utf-8")))
        self.assertTrue(defined, "DECISION_LOG.md defined no Dxxx entries")

        phantom: dict[str, list[str]] = {}
        for path in sorted(SRC.rglob("*.py")):
            for cited in set(_D_PATTERN.findall(path.read_text(encoding="utf-8"))):
                if cited not in defined:
                    phantom.setdefault(cited, []).append(
                        str(path.relative_to(PROJECT_ROOT))
                    )

        self.assertEqual(
            phantom,
            {},
            f"Phantom decision citations (cited in code, absent from DECISION_LOG): {phantom}",
        )


if __name__ == "__main__":
    unittest.main()
