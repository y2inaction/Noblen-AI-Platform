"""Paragraph-aware, deterministic chunking with configurable size/overlap.

Splits on paragraph boundaries first, then packs paragraphs into chunks up to
``chunk_size`` characters with ``chunk_overlap`` carried between chunks. PDF page
markers (``[[page:N]]`` inserted by the extractor) are consumed to attach page
numbers to chunk metadata (never invented — only recorded when present).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

_PAGE_MARKER = re.compile(r"^\[\[page:(\d+)\]\]\s*", re.MULTILINE)


@dataclass
class Chunk:
    index: int
    content: str
    character_count: int
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_paragraphs(text: str) -> list[tuple[str, int | None]]:
    """Return (paragraph, page_number|None) preserving order."""
    result: list[tuple[str, int | None]] = []
    current_page: int | None = None
    for block in text.split("\n\n"):
        marker = _PAGE_MARKER.match(block)
        if marker:
            current_page = int(marker.group(1))
            block = _PAGE_MARKER.sub("", block, count=1)
        block = block.strip()
        if block:
            result.append((block, current_page))
    return result


def chunk_text(
    text: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    max_chunks: int | None = None,
) -> list[Chunk]:
    size = chunk_size or settings.KNOWLEDGE_CHUNK_SIZE
    overlap = chunk_overlap if chunk_overlap is not None else settings.KNOWLEDGE_CHUNK_OVERLAP
    overlap = max(0, min(overlap, size // 2))
    limit = max_chunks or settings.MAX_CHUNKS_PER_DOCUMENT

    paragraphs = _split_paragraphs(text)
    chunks: list[Chunk] = []
    buffer = ""
    buffer_page: int | None = None

    def flush() -> None:
        nonlocal buffer
        content = buffer.strip()
        if not content:
            return
        chunks.append(
            Chunk(
                index=len(chunks),
                content=content,
                character_count=len(content),
                token_count=max(1, len(content.split())),
                metadata={"page_number": buffer_page} if buffer_page is not None else {},
            )
        )

    for paragraph, page in paragraphs:
        if len(chunks) >= limit:
            break
        # A single paragraph larger than the chunk size is hard-split.
        pieces = _hard_split(paragraph, size) if len(paragraph) > size else [paragraph]
        for piece in pieces:
            if buffer and len(buffer) + len(piece) + 2 > size:
                flush()
                if len(chunks) >= limit:
                    buffer = ""
                    break
                # Carry overlap tail into the next buffer.
                buffer = (buffer[-overlap:] + "\n\n") if overlap else ""
                buffer_page = page
            if not buffer:
                buffer_page = page
            buffer = f"{buffer}{piece}".strip() if not buffer else f"{buffer}\n\n{piece}"
    if len(chunks) < limit:
        flush()
    return chunks


def _hard_split(paragraph: str, size: int) -> list[str]:
    return [paragraph[i : i + size] for i in range(0, len(paragraph), size)]
