from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from hermes_cgm_agent.knowledge.ingest.pdf_loader import PageChunk, PdfManifestEntry
from hermes_cgm_agent.knowledge.ingest.pipeline import CandidateCard

logger = logging.getLogger(__name__)

PROMPT_PACKAGE = "hermes_cgm_agent.knowledge.ingest.prompts"
TEXT_PROMPT_NAME = "extract_claim_cards.txt"
VISION_PROMPT_NAME = "extract_claim_cards_vision.txt"


@dataclass(frozen=True)
class ExtractionAudit:
    page_no: int
    extraction_mode: str
    status: str
    candidate_count: int
    error: str | None = None


@dataclass
class HermesClaimExtractor:
    hermes_exe: str
    timeout_seconds: int = 300
    max_retries: int = 1
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None

    def extract_cards(
        self,
        *,
        pdf_meta: PdfManifestEntry,
        pages: list[PageChunk],
        kb_version: str,
    ) -> tuple[list[CandidateCard], list[ExtractionAudit]]:
        cards: list[CandidateCard] = []
        audits: list[ExtractionAudit] = []
        for page in pages:
            page_cards, audit = self._extract_page(
                pdf_meta=pdf_meta,
                page=page,
                kb_version=kb_version,
            )
            cards.extend(page_cards)
            audits.append(audit)
        return cards, audits

    def _extract_page(
        self,
        *,
        pdf_meta: PdfManifestEntry,
        page: PageChunk,
        kb_version: str,
    ) -> tuple[list[CandidateCard], ExtractionAudit]:
        mode = page.extraction_mode
        try:
            raw = self._call_hermes(page=page, pdf_meta=pdf_meta)
            parsed = self._parse_json_array(raw)
            cards = [
                self._to_candidate_card(
                    item,
                    pdf_meta=pdf_meta,
                    page=page,
                    kb_version=kb_version,
                )
                for item in parsed
            ]
            return cards, ExtractionAudit(
                page_no=page.page_no,
                extraction_mode=mode,
                status="ok",
                candidate_count=len(cards),
            )
        except Exception as exc:
            logger.warning("Hermes extraction failed for page %s: %s", page.page_no, exc)
            if mode == "vision" and (page.text.strip() or page.tables_md.strip()):
                try:
                    fallback_page = PageChunk(
                        page_no=page.page_no,
                        text=page.text,
                        tables_md=page.tables_md,
                        image_path=None,
                        extraction_mode="text",
                        source_path=page.source_path,
                    )
                    raw = self._call_hermes(page=fallback_page, pdf_meta=pdf_meta)
                    parsed = self._parse_json_array(raw)
                    cards = [
                        self._to_candidate_card(
                            item,
                            pdf_meta=pdf_meta,
                            page=fallback_page,
                            kb_version=kb_version,
                            extraction_mode="text",
                        )
                        for item in parsed
                    ]
                    return cards, ExtractionAudit(
                        page_no=page.page_no,
                        extraction_mode="text-fallback",
                        status="ok",
                        candidate_count=len(cards),
                    )
                except Exception as fallback_exc:
                    exc = fallback_exc
            return [], ExtractionAudit(
                page_no=page.page_no,
                extraction_mode=mode,
                status="error",
                candidate_count=0,
                error=str(exc),
            )

    def _call_hermes(self, *, page: PageChunk, pdf_meta: PdfManifestEntry) -> str:
        prompt = self._build_prompt(page=page, pdf_meta=pdf_meta)
        command = [
            self.hermes_exe,
            "chat",
            "-q",
            prompt,
            "-Q",
            "--max-turns",
            "1",
            "--toolsets",
            "",
        ]
        if page.extraction_mode in {"vision", "hybrid"} and page.image_path:
            command.extend(["--image", page.image_path])
        runner = self.runner or subprocess.run
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0 and not output:
            raise RuntimeError(f"Hermes exited with code {completed.returncode}")
        if not output:
            raise RuntimeError("Hermes returned empty output")
        return output

    def _build_prompt(self, *, page: PageChunk, pdf_meta: PdfManifestEntry) -> str:
        if page.extraction_mode in {"vision", "hybrid"}:
            template = _load_prompt(VISION_PROMPT_NAME)
        else:
            template = _load_prompt(TEXT_PROMPT_NAME)
        context = {
            "doc_title": pdf_meta.doc_title,
            "citation": pdf_meta.citation,
            "file_name": pdf_meta.file_name,
            "page_no": page.page_no,
            "default_population": pdf_meta.default_population,
            "default_tags": ", ".join(pdf_meta.default_tags),
            "page_text": page.text[:12000],
            "tables_md": page.tables_md[:8000],
            "extraction_mode": page.extraction_mode,
        }
        return template.format(**context)

    def _parse_json_array(self, raw: str) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return _coerce_json_array(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
        raise ValueError(f"Failed to parse Hermes JSON output: {last_error}")

    def _to_candidate_card(
        self,
        item: dict[str, Any],
        *,
        pdf_meta: PdfManifestEntry,
        page: PageChunk,
        kb_version: str,
        extraction_mode: str | None = None,
    ) -> CandidateCard:
        # Each page is an independent Hermes call with no cross-page memory, so a
        # model-chosen card_id (e.g. "tir-001") collides across pages and the
        # dedup would silently drop later pages. Always namespace by stem + page
        # so ids are globally unique while preserving the model's slug.
        raw_id = re.sub(r"[^a-zA-Z0-9]+", "-", str(item.get("card_id") or "").lower()).strip("-")
        card_id = (
            f"auto-{pdf_meta.stem}-p{page.page_no}-{raw_id[:48]}"
            if raw_id
            else _default_card_id(pdf_meta.stem, page.page_no, item)
        )
        source = dict(item.get("source") or {})
        source.setdefault("doc", pdf_meta.doc_title)
        source.setdefault("citation", pdf_meta.citation)
        source.setdefault("page", page.page_no)
        source.setdefault("kb_version", kb_version)
        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("extraction_mode", extraction_mode or page.extraction_mode)
        if item.get("source_evidence"):
            metadata.setdefault("source_evidence", item["source_evidence"])
        return CandidateCard(
            card_id=card_id,
            title=str(item.get("title") or card_id),
            claim_zh=str(item.get("claim_zh") or ""),
            claim_en=str(item.get("claim_en") or ""),
            population=str(item.get("population") or pdf_meta.default_population),
            tags=[str(tag) for tag in item.get("tags") or pdf_meta.default_tags],
            synonyms=[str(s) for s in item.get("synonyms") or []],
            source=source,
            verified=False,
            metadata=metadata,
        )


def _load_prompt(name: str) -> str:
    resource = resources.files(PROMPT_PACKAGE).joinpath(name)
    return resource.read_text(encoding="utf-8")


def _coerce_json_array(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON array found in Hermes output")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("Hermes output must be a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def _default_card_id(stem: str, page_no: int, item: dict[str, Any]) -> str:
    title = re.sub(r"[^a-zA-Z0-9]+", "-", str(item.get("title") or "claim").lower()).strip("-")
    return f"auto-{stem}-p{page_no}-{title[:40] or 'claim'}"
