from __future__ import annotations

import unittest

from hermes_cgm_agent.knowledge.ingest.pdf_loader import (
    PdfManifestEntry,
    parse_page_range,
    resolve_extraction_mode,
)


class PdfLoaderTests(unittest.TestCase):
    def test_parse_page_range(self) -> None:
        self.assertEqual(parse_page_range("1-3,5"), {1, 2, 3, 5})

    def test_resolve_vision_for_low_text(self) -> None:
        mode = resolve_extraction_mode(page_no=2, text="short", tables_md="")
        self.assertEqual(mode, "vision")

    def test_resolve_hybrid_for_table_signal(self) -> None:
        mode = resolve_extraction_mode(
            page_no=16,
            text="Table 1 shows TIR targets 70%",
            tables_md="",
        )
        self.assertEqual(mode, "hybrid")

    def test_manifest_vision_pages_force_vision(self) -> None:
        entry = PdfManifestEntry(
            file_name="ada.pdf",
            doc_title="ADA",
            citation="DC 2025",
            vision_pages=[12],
        )
        mode = resolve_extraction_mode(
            page_no=12,
            text="A" * 500,
            tables_md="",
            manifest_entry=entry,
        )
        self.assertEqual(mode, "vision")


if __name__ == "__main__":
    unittest.main()
