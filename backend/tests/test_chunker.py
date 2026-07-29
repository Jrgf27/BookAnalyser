"""Tests for the chapter chunker."""

from __future__ import annotations

from app.ingest.chunker import chunk_chapter


class TestChunker:
    def test_single_paragraph(self) -> None:
        text = "This is a short paragraph."
        chunks = chunk_chapter(text, chapter_number=1, target_tokens=600)
        assert len(chunks) == 1
        assert chunks[0]["text"] == text
        assert chunks[0]["chapter_number"] == 1
        assert chunks[0]["char_start"] == 0
        assert chunks[0]["char_end"] == len(text)

    def test_multiple_paragraphs_pack(self) -> None:
        # Two short paragraphs should fit in one chunk
        text = "First paragraph.\n\nSecond paragraph."
        chunks = chunk_chapter(text, chapter_number=5, target_tokens=600)
        assert len(chunks) == 1
        assert "First" in chunks[0]["text"]
        assert "Second" in chunks[0]["text"]

    def test_overflow_creates_new_chunk(self) -> None:
        # Many paragraphs should create multiple chunks
        paras = [f"Paragraph number {i}. " * 30 for i in range(20)]
        text = "\n\n".join(paras)
        chunks = chunk_chapter(text, chapter_number=1, target_tokens=100)
        assert len(chunks) > 1

    def test_overlap_exists(self) -> None:
        # With overlap, consecutive chunks should share some text
        paras = [f"Sentence {i} of the story. " * 20 for i in range(10)]
        text = "\n\n".join(paras)
        chunks = chunk_chapter(
            text, chapter_number=1, target_tokens=100, overlap_fraction=0.3
        )
        if len(chunks) >= 2:
            # Check that some content from the end of chunk 0 appears in chunk 1
            chunk0_lines = set(chunks[0]["text"].split("\n\n"))
            chunk1_lines = set(chunks[1]["text"].split("\n\n"))
            overlap = chunk0_lines & chunk1_lines
            assert len(overlap) > 0, "Expected overlapping paragraphs between chunks"

    def test_char_offsets_valid(self) -> None:
        text = "Para one.\n\nPara two.\n\nPara three."
        chunks = chunk_chapter(text, chapter_number=1, target_tokens=600)
        for c in chunks:
            assert c["char_start"] >= 0
            assert c["char_end"] <= len(text)
            assert c["char_start"] < c["char_end"]

    def test_empty_input(self) -> None:
        chunks = chunk_chapter("", chapter_number=1)
        assert chunks == []
