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
        # With overlap, consecutive chunks should share some text.
        # Use a common sentence repeated so each paragraph is identical —
        # the overlap mechanism keeps trailing paragraphs from the previous
        # chunk, so the same text should appear in both.
        paras = ["The quick brown fox jumps over the lazy dog. " * 15 for _ in range(10)]
        text = "\n\n".join(paras)
        chunks = chunk_chapter(
            text, chapter_number=1, target_tokens=100, overlap_fraction=0.3
        )
        assert len(chunks) >= 2, "Expected at least 2 chunks"
        # The last paragraph(s) of chunk 0 should appear at the start of chunk 1
        chunk0_end = chunks[0]["char_end"]
        chunk1_start = chunks[1]["char_start"]
        assert chunk1_start < chunk0_end, (
            f"Expected overlap: chunk0 ends at {chunk0_end}, "
            f"chunk1 starts at {chunk1_start}"
        )

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
