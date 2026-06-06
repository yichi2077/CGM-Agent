from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.knowledge.ingest.merge import merge_candidates_into_kb


class KbMergeTests(unittest.TestCase):
    def test_merge_forces_verified_false_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kb_path = Path(temp_dir) / "kb.json"
            kb_path.write_text(
                json.dumps(
                    {
                        "kb_version": "kb-test",
                        "cards": [
                            {
                                "card_id": "seed-1",
                                "title": "Seed",
                                "claim_zh": "种子",
                                "claim_en": "seed claim",
                                "population": "general",
                                "tags": [],
                                "synonyms": [],
                                "source": {"citation": "seed", "page": 1},
                                "verified": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            candidates = Path(temp_dir) / "candidates.json"
            candidates.write_text(
                json.dumps(
                    {
                        "cards": [
                            {
                                "card_id": "seed-1",
                                "title": "Dup",
                                "claim_zh": "重复",
                                "claim_en": "duplicate",
                                "source": {"citation": "x", "page": 2},
                                "verified": True,
                                "reviewer": "bot",
                            },
                            {
                                "card_id": "new-1",
                                "title": "New",
                                "claim_zh": "新",
                                "claim_en": "new claim with 70 percent tir",
                                "source": {"citation": "x", "page": 3},
                                "verified": True,
                                "reviewer": "bot",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            preview = merge_candidates_into_kb(
                candidates_path=candidates,
                kb_path=kb_path,
                dry_run=False,
            )
            merged = json.loads(kb_path.read_text(encoding="utf-8"))
        self.assertEqual(preview.added, ["new-1"])
        self.assertIn("seed-1", preview.skipped)
        self.assertEqual(len(merged["cards"]), 2)
        self.assertFalse(merged["cards"][1]["verified"])
        # Machine-ingested cards are tagged tier=auto (D041), and a verified=true
        # / reviewer in the candidate file must be stripped on the way in.
        self.assertEqual(merged["cards"][1]["tier"], "auto")
        self.assertNotIn("reviewer", merged["cards"][1])
        # The pre-existing seed card keeps its (default curated) tier.
        self.assertEqual(merged["cards"][0].get("tier", "curated"), "curated")


if __name__ == "__main__":
    unittest.main()
