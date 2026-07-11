from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.config import AppConfig
from hermes_cgm_agent.cli.status import _resolve_hermes_bin


def _default_pdf_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "knowledge" / "pdfs"


def _kb_ingest(*, pdf_path: Path, out_dir: Path, kb_version: str) -> int:
    from hermes_cgm_agent.knowledge.ingest import (
        build_candidate_cards,
        extract_pdf_text,
        write_candidate_json,
        write_review_markdown,
    )

    pages = extract_pdf_text(pdf_path)
    result = build_candidate_cards(
        source_path=pdf_path,
        pages=pages,
        kb_version=kb_version,
    )
    base = pdf_path.stem
    json_path = out_dir / f"{base}.candidates.json"
    review_path = out_dir / f"{base}.review.md"
    write_candidate_json(result, json_path)
    write_review_markdown(result, review_path)
    print(
        json.dumps(
            {
                "status": "ok",
                "source_path": str(pdf_path),
                "page_count": result.page_count,
                "candidate_count": result.candidate_count,
                "candidate_json": str(json_path),
                "review_markdown": str(review_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _kb_ingest_llm(
    *,
    config: AppConfig,
    pdf_path: Path,
    out_dir: Path,
    kb_version: str,
    pages: str | None,
    mode: str,
    engine: str,
) -> int:
    from hermes_cgm_agent.knowledge.ingest import (
        HermesClaimExtractor,
        PageChunk,
        build_sentence_candidates,
        extract_pdf_text,
        filter_candidates,
        find_manifest_entry,
        load_pdf_pages,
        parse_page_range,
        write_candidate_json,
        write_quality_markdown,
        write_review_markdown,
    )
    from hermes_cgm_agent.knowledge.ingest.pipeline import IngestResult
    from hermes_cgm_agent.services.rag import load_knowledge_base

    manifest = find_manifest_entry(pdf_path)
    page_filter = parse_page_range(pages)
    image_dir = out_dir / "_page_images" / pdf_path.stem
    audits: list[dict[str, object]] = []

    if engine == "sentence":
        page_texts = extract_pdf_text(pdf_path)
        if page_filter is not None:
            page_texts = [item for item in page_texts if item[0] in page_filter]
        result = build_sentence_candidates(
            source_path=pdf_path,
            pages=page_texts,
            kb_version=kb_version,
            citation=manifest.citation,
            doc_title=manifest.doc_title,
            population=manifest.default_population,
        )
        raw_candidates = result.candidates
        pages_by_no = {
            page_no: PageChunk(page_no=page_no, text=text, extraction_mode="text")
            for page_no, text in page_texts
        }
        chunks = []
    else:
        chunks = load_pdf_pages(
            pdf_path,
            manifest_entry=manifest,
            pages=page_filter,
            mode=mode,  # type: ignore[arg-type]
            image_dir=image_dir,
        )
        extractor = HermesClaimExtractor(
            hermes_exe=_resolve_hermes_bin(config.hermes_bin),
            timeout_seconds=config.timeout_seconds,
        )
        raw_candidates, extraction_audits = extractor.extract_cards(
            pdf_meta=manifest,
            pages=chunks,
            kb_version=kb_version,
        )
        audits = [
            {
                "page_no": item.page_no,
                "extraction_mode": item.extraction_mode,
                "status": item.status,
                "candidate_count": item.candidate_count,
                "error": item.error,
            }
            for item in extraction_audits
        ]
        pages_by_no = {chunk.page_no: chunk for chunk in chunks}

    quality = filter_candidates(
        raw_candidates,
        pages_by_no=pages_by_no,
        existing_cards=load_knowledge_base().cards,
    )
    ingest_result = IngestResult(
        source_path=str(pdf_path),
        page_count=len(chunks) if engine == "hermes" else len(page_texts),
        candidate_count=quality.accepted_count,
        candidates=quality.accepted,
    )

    base = pdf_path.stem
    json_path = out_dir / f"{base}.candidates.json"
    review_path = out_dir / f"{base}.review.md"
    quality_path = out_dir / f"{base}.quality.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_candidate_json(ingest_result, json_path)
    write_review_markdown(ingest_result, review_path)
    write_quality_markdown(quality, quality_path)
    print(
        json.dumps(
            {
                "status": "ok",
                "engine": engine,
                "source_path": str(pdf_path),
                "page_count": ingest_result.page_count,
                "raw_candidate_count": len(raw_candidates),
                "accepted_candidate_count": quality.accepted_count,
                "rejected_candidate_count": quality.rejected_count,
                "candidate_json": str(json_path),
                "review_markdown": str(review_path),
                "quality_markdown": str(quality_path),
                "audits": audits,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _kb_ingest_batch(
    *,
    config: AppConfig,
    out_dir: Path,
    kb_version: str,
    priority_min: int,
    mode: str,
    engine: str,
) -> int:
    from hermes_cgm_agent.knowledge.ingest import load_pdf_manifest

    pdf_dir = _default_pdf_dir()
    entries = [entry for entry in load_pdf_manifest() if entry.priority <= priority_min]
    results: list[dict[str, object]] = []
    for entry in entries:
        pdf_path = pdf_dir / entry.file_name
        if not pdf_path.exists():
            results.append({"file_name": entry.file_name, "status": "missing"})
            continue
        code = _kb_ingest_llm(
            config=config,
            pdf_path=pdf_path,
            out_dir=out_dir,
            kb_version=kb_version,
            pages=None,
            mode=mode,
            engine=engine,
        )
        results.append({"file_name": entry.file_name, "status": "ok" if code == 0 else "error"})
    print(json.dumps({"status": "ok", "processed": results}, ensure_ascii=False, indent=2))
    return 0


def _kb_merge(*, candidates_path: Path, into_path: Path | None, dry_run: bool, kb_version: str | None) -> int:
    from hermes_cgm_agent.knowledge.ingest import merge_candidates_into_kb

    files = (
        [candidates_path]
        if candidates_path.is_file()
        else sorted(candidates_path.glob("*.candidates.json"))
    )
    aggregate = {"added": [], "skipped": [], "total_after": 0, "kb_version": ""}
    target_kb = into_path
    for file_path in files:
        preview = merge_candidates_into_kb(
            candidates_path=file_path,
            kb_path=target_kb,
            dry_run=dry_run,
            kb_version=kb_version,
        )
        aggregate["added"].extend(preview.added)
        aggregate["skipped"].extend(preview.skipped)
        aggregate["total_after"] = preview.total_after
        aggregate["kb_version"] = preview.kb_version
        if not dry_run:
            from hermes_cgm_agent.knowledge.ingest.merge import DEFAULT_KB_PATH

            target_kb = into_path or DEFAULT_KB_PATH
    print(json.dumps({"status": "ok", "dry_run": dry_run, **aggregate}, ensure_ascii=False, indent=2))
    return 0


def _kb_pending(*, kb_path: Path | None, output_format: str, limit: int | None) -> int:
    from hermes_cgm_agent.services.rag import load_knowledge_base

    kb = load_knowledge_base(kb_path)
    rows = [
        {
            "card_id": card.card_id,
            "title": card.title,
            "tier": card.tier,
            "source": card.source.get("citation") or card.source.get("doc") or "",
            "page": card.source.get("page"),
        }
        for card in kb.cards
        if not card.verified
    ]
    if limit is not None:
        rows = rows[: max(0, limit)]
    if output_format == "json":
        print(json.dumps({"kb_version": kb.kb_version, "pending": rows}, ensure_ascii=False, indent=2))
        return 0
    print("card_id\ttitle\ttier\tsource\tpage")
    for row in rows:
        print(
            f"{row['card_id']}\t{row['title']}\t{row['tier']}\t{row['source']}\t{row['page'] or ''}"
        )
    return 0


def _kb_approve_cli(
    *,
    kb_path: Path | None,
    card_id: str,
    reviewer: str,
    reviewed_at: str | None,
) -> int:
    from hermes_cgm_agent.services.rag import AuthoritativeRAGService

    result = AuthoritativeRAGService(kb_path=kb_path).approve(
        card_id=card_id,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
    )
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


def _eval_rag(
    *,
    queries_path: Path,
    kb_path: Path | None,
    min_hit3: float | None = None,
    emit_report: bool = True,
) -> int:
    from hermes_cgm_agent.services.rag.eval_hit3 import evaluate_hit3

    report = evaluate_hit3(queries_path=queries_path, kb_path=kb_path)
    if min_hit3 is not None:
        report["min_hit3"] = min_hit3
        report["passed"] = report["hit_at_3"] >= min_hit3
    if emit_report:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if min_hit3 is not None and report["hit_at_3"] < min_hit3:
        return 1
    return 0
