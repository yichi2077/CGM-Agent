from __future__ import annotations

from hermes_cgm_agent.knowledge.ingest.hermes_extractor import (
    ExtractionAudit,
    HermesClaimExtractor,
)
from hermes_cgm_agent.knowledge.ingest.merge import MergePreview, merge_candidates_into_kb
from hermes_cgm_agent.knowledge.ingest.pdf_loader import (
    PageChunk,
    PdfManifestEntry,
    find_manifest_entry,
    load_pdf_manifest,
    load_pdf_pages,
    parse_page_range,
    resolve_extraction_mode,
)
from hermes_cgm_agent.knowledge.ingest.pipeline import (
    CandidateCard,
    IngestResult,
    build_candidate_cards,
    build_sentence_candidates,
    extract_pdf_text,
    write_candidate_json,
    write_review_markdown,
)
from hermes_cgm_agent.knowledge.ingest.quality import QualityReport, filter_candidates, write_quality_markdown

__all__ = [
    "CandidateCard",
    "ExtractionAudit",
    "HermesClaimExtractor",
    "IngestResult",
    "MergePreview",
    "PageChunk",
    "PdfManifestEntry",
    "QualityReport",
    "build_candidate_cards",
    "build_sentence_candidates",
    "extract_pdf_text",
    "filter_candidates",
    "find_manifest_entry",
    "load_pdf_manifest",
    "load_pdf_pages",
    "merge_candidates_into_kb",
    "parse_page_range",
    "resolve_extraction_mode",
    "write_candidate_json",
    "write_quality_markdown",
    "write_review_markdown",
]
