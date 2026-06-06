from __future__ import annotations

import unittest

from hermes_cgm_agent.knowledge.ingest.pdf_loader import PageChunk
from hermes_cgm_agent.knowledge.ingest.pipeline import CandidateCard
from hermes_cgm_agent.knowledge.ingest.quality import filter_candidates


class KbQualityFilterTests(unittest.TestCase):
    def test_rejects_author_page_noise(self) -> None:
        candidate = CandidateCard(
            card_id="noise-1",
            title="Authors",
            claim_zh="作者",
            claim_en="AUTHOR LIST FOR REFERENCES AND CORRESPONDENCE",
            source={"citation": "x", "page": 1},
        )
        report = filter_candidates([candidate], pages_by_no={1: PageChunk(page_no=1, text="AUTHOR LIST")})
        self.assertEqual(report.accepted_count, 0)

    def test_rejects_unsupported_text_claim(self) -> None:
        candidate = CandidateCard(
            card_id="bad-1",
            title="Fake",
            claim_zh="假",
            claim_en="Completely invented threshold 99 percent for all populations",
            tags=["TIR"],
            source={"citation": "x", "page": 2},
        )
        page = PageChunk(page_no=2, text="Unrelated page content without that number.")
        report = filter_candidates([candidate], pages_by_no={2: page})
        self.assertEqual(report.accepted_count, 0)

    def test_rejects_mojibake_glyphs(self) -> None:
        # Broken CID-font extraction (e.g. a Chinese PDF) renders PUA glyphs.
        # U+1001B0 is the actual PUA-B glyph produced by the broken CDS CID font.
        garbled = "中国老年糖尿病诊疗指南 （２０２４ 版） Ｖｏｌ\U001001b0１５ Ｎｏ ４ ７７３ 目标 70"
        candidate = CandidateCard(
            card_id="mojibake-1",
            title="garble",
            claim_zh="待翻译: " + garbled,
            claim_en=garbled,
            tags=["target"],
            source={"citation": "x", "page": 3},
            metadata={"extraction_mode": "text-heuristic"},
        )
        report = filter_candidates(
            [candidate], pages_by_no={3: PageChunk(page_no=3, text=garbled)}
        )
        self.assertEqual(report.accepted_count, 0)

    def test_rejects_bibliographic_metadata_line(self) -> None:
        line = "Horm Res Paediatr 2024;97:546 DOI: 10.1159/000543266 target range 70 percent"
        candidate = CandidateCard(
            card_id="meta-1",
            title="masthead",
            claim_zh="待翻译: " + line,
            claim_en=line,
            tags=["target"],
            source={"citation": "x", "page": 1},
            metadata={"extraction_mode": "text-heuristic"},
        )
        report = filter_candidates(
            [candidate], pages_by_no={1: PageChunk(page_no=1, text=line)}
        )
        self.assertEqual(report.accepted_count, 0)

    def test_rejects_page_number_title_fragment(self) -> None:
        frag = "5 ABSTRACT Improvements in sensor accuracy have led to 70 percent adoption."
        candidate = CandidateCard(
            card_id="title-1",
            title="frag",
            claim_zh="待翻译: " + frag,
            claim_en=frag,
            tags=["target"],
            source={"citation": "x", "page": 5},
            metadata={"extraction_mode": "text-heuristic"},
        )
        report = filter_candidates(
            [candidate], pages_by_no={5: PageChunk(page_no=5, text=frag)}
        )
        self.assertEqual(report.accepted_count, 0)

    def test_accepts_supported_sentence_candidate(self) -> None:
        sentence = "For most adults with diabetes, Time in Range (70-180 mg/dL) target >70%."
        candidate = CandidateCard(
            card_id="good-1",
            title="TIR",
            claim_zh="待翻译: " + sentence,
            claim_en=sentence,
            tags=["TIR"],
            source={"citation": "x", "page": 3},
            metadata={"extraction_mode": "text-heuristic"},
        )
        page = PageChunk(page_no=3, text=sentence)
        report = filter_candidates([candidate], pages_by_no={3: page})
        self.assertEqual(report.accepted_count, 1)


if __name__ == "__main__":
    unittest.main()
