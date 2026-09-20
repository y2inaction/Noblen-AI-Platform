"""Unit tests for extraction, cleaning, and chunking (no database required)."""

import pytest

from app.knowledge import chunking, cleaning, extraction
from app.knowledge.errors import UnsupportedDocumentType


def test_extract_text_and_markdown():
    doc = extraction.extract(b"Hello world", filename="a.txt", mime_type="text/plain")
    assert doc.text == "Hello world"
    md = extraction.extract(b"# Title\n\nBody", filename="a.md")
    assert "Title" in md.text


def test_extract_unsupported_type_raises():
    with pytest.raises(UnsupportedDocumentType):
        extraction.extract(b"\x00\x01", filename="a.bin", mime_type="application/octet-stream")


def test_cleaning_collapses_whitespace_preserves_paragraphs():
    raw = "Line1   with   spaces\n\n\n\nLine2\t\ttabs   "
    cleaned = cleaning.clean_text(raw)
    assert "   " not in cleaned
    assert "\n\n\n" not in cleaned
    assert "Line1 with spaces" in cleaned
    assert "\n\nLine2" in cleaned  # paragraph break preserved


def test_chunking_deterministic_and_bounded():
    text = "\n\n".join(f"Paragraph number {i} with some words." for i in range(20))
    chunks = chunking.chunk_text(text, chunk_size=120, chunk_overlap=20)
    assert len(chunks) >= 2
    # Deterministic ordering.
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # Re-running yields identical output.
    again = chunking.chunk_text(text, chunk_size=120, chunk_overlap=20)
    assert [c.content for c in chunks] == [c.content for c in again]
    for c in chunks:
        assert c.character_count == len(c.content)
        assert c.token_count >= 1


def test_chunking_records_page_numbers():
    text = "[[page:1]]\nPage one content here.\n\n[[page:2]]\nPage two content here."
    chunks = chunking.chunk_text(text, chunk_size=1000, chunk_overlap=0)
    pages = {c.metadata.get("page_number") for c in chunks}
    assert 1 in pages


def test_chunking_respects_max_chunks():
    text = "\n\n".join(f"Para {i}" for i in range(100))
    chunks = chunking.chunk_text(text, chunk_size=10, chunk_overlap=0, max_chunks=5)
    assert len(chunks) <= 5
