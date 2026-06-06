from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Literal

from hermes_cgm_agent.knowledge.ingest.pipeline import extract_pdf_text

ExtractionMode = Literal["auto", "text", "vision", "hybrid"]
ResolvedExtractionMode = Literal["text", "vision", "hybrid"]

MANIFEST_RESOURCE_PACKAGE = "hermes_cgm_agent.knowledge"
MANIFEST_RESOURCE_NAME = "pdf_manifest.json"


@dataclass(frozen=True)
class PdfManifestEntry:
    file_name: str
    doc_title: str
    citation: str
    priority: int = 3
    vision_pages: list[int] = field(default_factory=list)
    default_population: str = "general"
    default_tags: list[str] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return Path(self.file_name).stem


@dataclass(frozen=True)
class PageChunk:
    page_no: int
    text: str = ""
    tables_md: str = ""
    image_path: str | None = None
    extraction_mode: ResolvedExtractionMode = "text"
    source_path: str | None = None


def load_pdf_manifest(path: str | Path | None = None) -> list[PdfManifestEntry]:
    if path is not None:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        resource = resources.files(MANIFEST_RESOURCE_PACKAGE).joinpath(MANIFEST_RESOURCE_NAME)
        raw = resource.read_text(encoding="utf-8")
    data = json.loads(raw)
    entries = data.get("pdfs", data if isinstance(data, list) else [])
    return [PdfManifestEntry(**entry) for entry in entries]


def find_manifest_entry(
    pdf_path: str | Path,
    entries: list[PdfManifestEntry] | None = None,
) -> PdfManifestEntry:
    path = Path(pdf_path)
    entries = entries or load_pdf_manifest()
    for entry in entries:
        if entry.file_name == path.name:
            return entry
    return PdfManifestEntry(
        file_name=path.name,
        doc_title=path.stem,
        citation=path.stem,
        priority=99,
    )


def parse_page_range(raw: str | None) -> set[int] | None:
    if not raw:
        return None
    selected: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            selected.update(range(int(start), int(end) + 1))
        else:
            selected.add(int(part))
    return selected


def load_pdf_pages(
    pdf_path: str | Path,
    *,
    manifest_entry: PdfManifestEntry | None = None,
    pages: set[int] | None = None,
    mode: ExtractionMode = "auto",
    image_dir: str | Path | None = None,
    render_images: bool = True,
) -> list[PageChunk]:
    pdf = Path(pdf_path)
    manifest_entry = manifest_entry or find_manifest_entry(pdf)
    page_texts = extract_pdf_text(pdf)
    tables_by_page = extract_tables_markdown(pdf)
    chunks: list[PageChunk] = []
    for page_no, text in page_texts:
        if pages is not None and page_no not in pages:
            continue
        tables_md = tables_by_page.get(page_no, "")
        resolved = resolve_extraction_mode(
            page_no=page_no,
            text=text,
            tables_md=tables_md,
            manifest_entry=manifest_entry,
            requested=mode,
        )
        image_path: str | None = None
        if render_images and resolved in {"vision", "hybrid"}:
            image_path = render_page_png(pdf, page_no=page_no, image_dir=image_dir)
        chunks.append(
            PageChunk(
                page_no=page_no,
                text=text or "",
                tables_md=tables_md,
                image_path=image_path,
                extraction_mode=resolved,
                source_path=str(pdf),
            )
        )
    return chunks


def resolve_extraction_mode(
    *,
    page_no: int,
    text: str,
    tables_md: str = "",
    manifest_entry: PdfManifestEntry | None = None,
    requested: ExtractionMode = "auto",
) -> ResolvedExtractionMode:
    if requested in {"text", "vision"}:
        return requested
    if requested == "hybrid":
        return "hybrid"
    if manifest_entry and page_no in set(manifest_entry.vision_pages):
        return "vision"
    stripped = (text or "").strip()
    if tables_md.strip():
        return "vision"
    if _has_visual_table_signal(stripped):
        return "hybrid"
    if len(stripped) < 120:
        return "vision"
    return "text"


def extract_tables_markdown(pdf_path: str | Path) -> dict[int, str]:
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return {}

    tables: dict[int, str] = {}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                page_tables = page.extract_tables() or []
                rendered = [_table_to_markdown(table) for table in page_tables if table]
                if rendered:
                    tables[index] = "\n\n".join(rendered)
    except Exception:
        return {}
    return tables


def render_page_png(
    pdf_path: str | Path,
    *,
    page_no: int,
    image_dir: str | Path | None = None,
    dpi: int = 180,
) -> str:
    out_dir = Path(image_dir) if image_dir is not None else Path(tempfile.gettempdir()) / "kb-ingest-images"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{Path(pdf_path).stem}-p{page_no:03d}.png"
    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError("Vision extraction requires the optional 'pymupdf' dependency.") from exc
    try:
        doc = fitz.open(str(pdf_path))
        page = doc.load_page(page_no - 1)
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(str(out))
        doc.close()
    except Exception as exc:
        raise RuntimeError(f"Failed to render {pdf_path} page {page_no} to PNG.") from exc
    return str(out)


def _has_visual_table_signal(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\b(table|figure|fig\.|图|表)\s*\d+", lowered):
        return True
    return any(token in lowered for token in ("tir", "tbr", "tar", "mg/dl", "mmol/l")) and "%" in lowered


def _table_to_markdown(table: list[list[object]]) -> str:
    rows = [["" if cell is None else str(cell).strip() for cell in row] for row in table]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    sep = ["---"] * width
    body = padded[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)
