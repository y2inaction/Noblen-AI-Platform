"""Document extraction: bytes → normalized text + metadata.

Supported: TXT, MD, PDF (pypdf), DOCX (python-docx), and (M3) CSV / XLSX (openpyxl),
which also yield typed tables for structured queries. Unsupported types
raise a clear validation error. Uploaded files are treated strictly as data — never
executed.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.errors import DocumentExtractionError, UnsupportedDocumentType
from app.knowledge.tabular import ExtractedTable, parse_csv, parse_xlsx, render_text

# Extension → canonical kind
_EXT_KIND = {
    "txt": "text",
    "text": "text",
    "md": "markdown",
    "markdown": "markdown",
    "pdf": "pdf",
    "docx": "docx",
    "csv": "csv",
    "xlsx": "xlsx",
}
# MIME → canonical kind (used as a secondary signal)
_MIME_KIND = {
    "text/plain": "text",
    "text/markdown": "markdown",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/csv": "csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


@dataclass
class ExtractedDocument:
    text: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Structured tables for CSV/XLSX sources (queried exactly, not by similarity).
    tables: list[ExtractedTable] = field(default_factory=list)


def _detect_kind(*, filename: str | None, mime_type: str | None) -> str | None:
    if mime_type and mime_type.lower() in _MIME_KIND:
        return _MIME_KIND[mime_type.lower()]
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in _EXT_KIND:
            return _EXT_KIND[ext]
    return None


def _extract_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise DocumentExtractionError(f"Could not read PDF: {type(exc).__name__}") from exc
    # Join pages with markers so chunk metadata can record page numbers.
    text = "\n\n".join(f"[[page:{i + 1}]]\n{p}" for i, p in enumerate(pages))
    return text, {"page_count": len(pages)}


def _extract_docx(data: bytes) -> tuple[str, dict[str, Any]]:
    try:
        import docx

        document = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as exc:  # noqa: BLE001
        raise DocumentExtractionError(f"Could not read DOCX: {type(exc).__name__}") from exc
    return "\n\n".join(paragraphs), {"paragraph_count": len(paragraphs)}


def extract(
    data: bytes, *, filename: str | None = None, mime_type: str | None = None
) -> ExtractedDocument:
    """Extract normalized text + metadata from raw bytes."""
    kind = _detect_kind(filename=filename, mime_type=mime_type)
    if kind is None:
        raise UnsupportedDocumentType(
            f"Unsupported document type (filename={filename!r}, mime={mime_type!r}). "
            "Supported: TXT, MD, PDF, DOCX, CSV, XLSX."
        )

    title = filename.rsplit(".", 1)[0] if filename else None
    if kind in ("text", "markdown"):
        return ExtractedDocument(text=_extract_text(data), title=title, metadata={"kind": kind})
    if kind == "pdf":
        text, meta = _extract_pdf(data)
        return ExtractedDocument(text=text, title=title, metadata={"kind": kind, **meta})
    if kind == "docx":
        text, meta = _extract_docx(data)
        return ExtractedDocument(text=text, title=title, metadata={"kind": kind, **meta})
    if kind in ("csv", "xlsx"):
        tables = parse_csv(data, title or "Sheet1") if kind == "csv" else parse_xlsx(data)
        if not tables:
            raise DocumentExtractionError("The spreadsheet has no rows.")
        return ExtractedDocument(
            text=render_text(tables),
            title=title,
            metadata={
                "kind": kind,
                "tables": [
                    {"name": t.name, "rows": len(t.rows), "truncated": t.truncated} for t in tables
                ],
            },
            tables=tables,
        )
    raise UnsupportedDocumentType(f"Unsupported document kind '{kind}'.")
