from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import MagicMock

from hermes_cgm_agent.knowledge.ingest.hermes_extractor import (
    HermesClaimExtractor,
    _coerce_json_array,
)
from hermes_cgm_agent.knowledge.ingest.pdf_loader import PageChunk, PdfManifestEntry


class HermesExtractorTests(unittest.TestCase):
    def test_coerce_json_array_from_code_fence(self) -> None:
        raw = """Here are cards:
```json
[{"card_id":"x","title":"T","claim_en":"A","claim_zh":"B","population":"general","tags":[],"synonyms":[],"source":{"page":1}}]
```"""
        parsed = _coerce_json_array(raw)
        self.assertEqual(parsed[0]["card_id"], "x")

    def test_extract_cards_uses_image_for_vision_mode(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([]), stderr="")

        extractor = HermesClaimExtractor(hermes_exe="hermes", runner=runner)
        page = PageChunk(
            page_no=12,
            text="",
            tables_md="| TIR | >70% |",
            image_path="/tmp/p12.png",
            extraction_mode="vision",
        )
        meta = PdfManifestEntry(
            file_name="ada.pdf",
            doc_title="ADA",
            citation="Diabetes Care 2025",
        )
        cards, audits = extractor.extract_cards(pdf_meta=meta, pages=[page], kb_version="kb-test")
        self.assertEqual(cards, [])
        self.assertEqual(audits[0].status, "ok")
        self.assertIn("--image", calls[0])
        self.assertIn("/tmp/p12.png", calls[0])

    def test_extract_cards_parses_valid_json(self) -> None:
        def runner(command, **kwargs):
            payload = [
                {
                    "card_id": "ok",
                    "title": "T",
                    "claim_en": "TIR target >70%",
                    "claim_zh": "TIR 目标 >70%",
                    "population": "general",
                    "tags": ["TIR"],
                    "synonyms": [],
                    "source": {"page": 3},
                }
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        extractor = HermesClaimExtractor(hermes_exe="hermes", runner=runner)
        page = PageChunk(page_no=3, text="TIR target >70%", extraction_mode="text")
        meta = PdfManifestEntry(file_name="tir.pdf", doc_title="TIR", citation="DC 2019")
        cards, audits = extractor.extract_cards(pdf_meta=meta, pages=[page], kb_version="kb-test")
        self.assertEqual(len(cards), 1)
        # card_id is namespaced by stem + page so per-page model ids stay unique.
        self.assertEqual(cards[0].card_id, "auto-tir-p3-ok")
        self.assertEqual(audits[0].candidate_count, 1)


if __name__ == "__main__":
    unittest.main()
