"""D059: deterministic claim-card enrichment (synonyms + bilingual/number check)."""
from __future__ import annotations

import unittest

from hermes_cgm_agent.knowledge.ingest.enrich import (
    enrich_card,
    enrich_synonyms,
    verify_bilingual_and_numbers,
)


class EnrichSynonymsTests(unittest.TestCase):
    def test_cross_unit_dual_write_mgdl_to_mmol(self) -> None:
        card = {
            "card_id": "hypo-levels",
            "title": "Hypoglycemia levels",
            "claim_zh": "1级低血糖 <70 mg/dL。",
            "claim_en": "Level 1 hypoglycemia is <70 mg/dL.",
            "tags": ["hypoglycemia"],
            "synonyms": [],
        }
        syns = enrich_synonyms(card)
        # 70 mg/dL ≈ 3.9 mmol/L — the mmol reader must still recall this card.
        self.assertIn("3.9 mmol/L", syns)

    def test_cross_unit_dual_write_range_both_endpoints(self) -> None:
        card = {
            "card_id": "tir-def",
            "title": "TIR range",
            "claim_zh": "目标范围 70–180 mg/dL。",
            "claim_en": "Target range is 70-180 mg/dL.",
            "tags": ["TIR"],
            "synonyms": [],
        }
        syns = enrich_synonyms(card)
        self.assertIn("3.9 mmol/L", syns)   # 70 -> 3.9
        self.assertIn("10.0 mmol/L", syns)  # 180 -> 10.0

    def test_bilingual_term_pair_injected(self) -> None:
        card = {
            "card_id": "gmi",
            "title": "GMI",
            "claim_zh": "血糖管理指标反映平均血糖。",
            "claim_en": "GMI reflects mean glucose.",
            "tags": ["GMI"],
            "synonyms": [],
        }
        syns = enrich_synonyms(card)
        self.assertIn("GMI", syns)
        self.assertIn("血糖管理指标", syns)

    def test_colloquial_phrases_by_topic(self) -> None:
        card = {
            "card_id": "hypo-15-15",
            "title": "15-15 rule",
            "claim_zh": "低血糖时吃 15 克碳水，等 15 分钟复测。",
            "claim_en": "For hypoglycemia eat 15 g carbohydrate, recheck in 15 minutes.",
            "tags": ["hypoglycemia", "treatment"],
            "synonyms": [],
        }
        syns = enrich_synonyms(card)
        self.assertIn("血糖低了", syns)
        self.assertIn("手抖", syns)

    def test_existing_synonyms_preserved_and_deduped(self) -> None:
        card = {
            "card_id": "x",
            "title": "t",
            "claim_zh": "高血糖 >250 mg/dL 查酮体。",
            "claim_en": "Hyperglycemia >250 mg/dL check ketone.",
            "tags": ["hyperglycemia"],
            "synonyms": ["酮体", "酮体"],  # duplicate + collides with term-pair
        }
        syns = enrich_synonyms(card)
        self.assertEqual(syns[0], "酮体")           # existing leads
        self.assertEqual(sum(1 for s in syns if s == "酮体"), 1)  # deduped

    def test_enrich_card_does_not_mutate_input(self) -> None:
        card = {"card_id": "x", "title": "t", "claim_zh": "低血糖", "claim_en": "hypo", "synonyms": []}
        out = enrich_card(card)
        self.assertIsNot(out, card)
        self.assertEqual(card["synonyms"], [])
        self.assertGreater(len(out["synonyms"]), 0)


class VerifyBilingualNumbersTests(unittest.TestCase):
    def test_clean_card_has_no_problems(self) -> None:
        card = {
            "card_id": "ok",
            "claim_zh": "TIR 目标 >70%，TBR <4%。",
            "claim_en": "TIR target >70%, TBR <4%.",
        }
        self.assertEqual(verify_bilingual_and_numbers(card), [])

    def test_unit_converted_numbers_are_consistent(self) -> None:
        card = {
            "card_id": "unit-ok",
            "claim_zh": "低血糖阈值为 70 mg/dL。",
            "claim_en": "Hypoglycemia threshold is 3.9 mmol/L.",
        }
        self.assertEqual(verify_bilingual_and_numbers(card), [])

    def test_dropped_number_in_translation_flagged(self) -> None:
        card = {
            "card_id": "bad",
            "claim_zh": "低血糖吃 15 克碳水，等 15 分钟。",
            "claim_en": "For hypoglycemia eat carbohydrate and wait.",  # numbers dropped
        }
        problems = verify_bilingual_and_numbers(card)
        self.assertTrue(any("15" in p for p in problems))

    def test_empty_side_flagged(self) -> None:
        card = {"card_id": "e", "claim_zh": "有内容", "claim_en": ""}
        problems = verify_bilingual_and_numbers(card)
        self.assertTrue(any("claim_en is empty" in p for p in problems))

    def test_placeholder_side_flagged(self) -> None:
        card = {"card_id": "p", "claim_zh": "待人工翻译/核验: something", "claim_en": "real text 70"}
        problems = verify_bilingual_and_numbers(card)
        self.assertTrue(any("placeholder" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
