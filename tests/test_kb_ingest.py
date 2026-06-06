from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.knowledge.ingest import (
    build_candidate_cards,
    write_candidate_json,
    write_review_markdown,
)


class KnowledgeIngestTests(unittest.TestCase):
    def test_build_candidate_cards_from_page_text(self) -> None:
        result = build_candidate_cards(
            source_path="ada-2025.pdf",
            kb_version="kb-test",
            pages=[
                (
                    12,
                    "Hypoglycemia level 1 is 54-70 mg/dL. The 15-15 rule uses "
                    "fast-acting carbohydrate and repeat checks.",
                )
            ],
        )

        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.candidate_count, 1)
        card = result.candidates[0]
        self.assertFalse(card.verified)
        self.assertEqual(card.source["page"], 12)
        self.assertIn("hypoglycemia", card.tags)

    def test_writes_candidate_json_and_review_markdown(self) -> None:
        result = build_candidate_cards(
            source_path="tir.pdf",
            kb_version="kb-test",
            pages=[(1, "Time in range target should be reviewed by population.")],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "candidates.json"
            review_path = Path(temp_dir) / "review.md"

            write_candidate_json(result, json_path)
            write_review_markdown(result, review_path)

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            review = review_path.read_text(encoding="utf-8")

        self.assertEqual(payload["candidate_count"], 1)
        self.assertIn("Source page checked", review)
        self.assertEqual(payload["cards"][0]["verified"], False)


if __name__ == "__main__":
    unittest.main()
