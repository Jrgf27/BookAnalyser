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
        # Paragraphs must be small enough that at least one fits inside the
        # overlap budget, otherwise there is genuinely nothing to carry over.
        # Here each paragraph is ~6 tokens, target 30, overlap 0.5 → ~2
        # paragraphs of real overlap between consecutive chunks.
        paras = [f"Sentence number {i} appears right here." for i in range(30)]
        text = "\n\n".join(paras)
        chunks = chunk_chapter(
            text, chapter_number=1, target_tokens=30, overlap_fraction=0.5
        )
        assert len(chunks) >= 2, "Expected at least 2 chunks"
        # Genuine overlap: chunk 1 starts before chunk 0 ends (char ranges),
        # and the two chunks share at least one paragraph of text.
        chunk0_end = chunks[0]["char_end"]
        chunk1_start = chunks[1]["char_start"]
        assert chunk1_start < chunk0_end
        shared = {p for p in paras if p in chunks[0]["text"] and p in chunks[1]["text"]}
        assert shared, "Expected consecutive chunks to share paragraph text"

    def test_char_offsets_valid(self) -> None:
        text = "Para one.\n\nPara two.\n\nPara three."
        chunks = chunk_chapter(text, chapter_number=1, target_tokens=600)
        for c in chunks:
            assert c["char_start"] >= 0
            assert c["char_end"] <= len(text)
            assert c["char_start"] < c["char_end"]

    def test_char_offsets_correct_with_repeated_paragraphs(self) -> None:
        # Regression: identical repeated paragraphs used to all resolve to the
        # first occurrence's offset (str.find from 0).  Offsets must be
        # monotonic and each chunk's span must actually contain its own text.
        unit = "she said quietly to herself in the dark"
        text = "\n\n".join([unit] * 12)
        chunks = chunk_chapter(
            text, chapter_number=1, target_tokens=40, overlap_fraction=0.15
        )
        assert len(chunks) > 1
        prev_start = -1
        for c in chunks:
            # char_start advances (allowing equal only is not expected here)
            assert c["char_start"] > prev_start
            prev_start = c["char_start"]
            # The declared span is wide enough to hold the chunk's text and the
            # first unit of the chunk really is at char_start.
            assert c["char_end"] - c["char_start"] >= len(unit)
            assert text[c["char_start"]:].startswith(unit)

    def test_empty_input(self) -> None:
        chunks = chunk_chapter("", chapter_number=1)
        assert chunks == []
