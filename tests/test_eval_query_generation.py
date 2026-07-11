from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.gen_eval_queries import generate_queries


class EvalQueryGenerationTests(unittest.TestCase):
    def test_generates_priority_card_queries_and_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kb_path = Path(temp_dir) / "kb.json"
            kb_path.write_text(
                json.dumps(
                    {
                        "kb_version": "kb-test",
                        "cards": [
                            {
                                "card_id": "ada-hypo",
                                "title": "Hypoglycemia 15-15",
                                "claim_zh": "低血糖时使用 15 克碳水并在 15 分钟后复测。",
                                "claim_en": "Use 15 g carbohydrate for hypoglycemia and recheck in 15 minutes.",
                                "population": "general",
                                "tags": ["hypoglycemia"],
                                "synonyms": ["低血糖", "15 克碳水"],
                                "tier": "auto",
                                "verified": False,
                            },
                            {
                                "card_id": "plain",
                                "title": "Plain",
                                "claim_zh": "一般文字。",
                                "claim_en": "Plain text.",
                                "population": "general",
                                "tags": [],
                                "synonyms": [],
                                "tier": "auto",
                                "verified": False,
                            },
                            {
                                "card_id": "cdc-dka",
                                "title": "DKA ketone testing",
                                "claim_zh": "血糖 250 mg/dL 或 13.9 mmol/L 以上时查酮体。",
                                "claim_en": "Check ketones when glucose is 250 mg/dL or 13.9 mmol/L or above.",
                                "population": "general",
                                "tags": ["DKA", "ketone"],
                                "synonyms": ["血糖 250 查酮"],
                                "tier": "auto",
                                "verified": False,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            existing = [{"query": "seed", "expected_any": ["seed-card"], "track": "authoritative_kb"}]

            rows = generate_queries(kb_path, existing=existing, max_cards=10, queries_per_card=2)

        self.assertEqual(rows[0], existing[0])
        generated = [row for row in rows if row.get("generated_by") == "scripts/gen_eval_queries.py"]
        self.assertTrue(any(row["expected_any"] == ["ada-hypo"] for row in generated))
        self.assertTrue(any(row["expected_any"] == ["cdc-dka"] for row in generated))
        self.assertFalse(any(row["expected_any"] == ["plain"] for row in generated))
        self.assertTrue(any("低血糖" in row["query"] or "Hypoglycemia" in row["query"] for row in generated))


if __name__ == "__main__":
    unittest.main()
