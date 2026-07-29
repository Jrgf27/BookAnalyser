"""Tests for the Gutenberg HTML parser."""

from __future__ import annotations

import pytest

from app.ingest.parser import (
    GutenbergParser,
    roman_to_int,
    _match_chapter_heading,
)


class TestMatchChapterHeading:
    def test_roman_lead_with_title(self) -> None:
        # Little Women style: "IX. The Laurence Boy."
        assert _match_chapter_heading("IX. The Laurence Boy.") == (9, "The Laurence Boy.")

    def test_chapter_keyword(self) -> None:
        assert _match_chapter_heading("CHAPTER IV.") == (4, "")

    def test_caption_prefix(self) -> None:
        # Pride & Prejudice style: illustration caption before the real marker.
        assert _match_chapter_heading(
            "I hope Mr. Bingley will like it. CHAPTER II."
        ) == (2, "")

    def test_no_space_after_chapter(self) -> None:
        # "CHAPTERXXVII." — the two run together in some editions.
        assert _match_chapter_heading("“On the Stairs.” CHAPTERXXVII.") == (27, "")

    def test_non_chapter_headings_ignored(self) -> None:
        # Words that merely start with a roman letter must not match.
        assert _match_chapter_heading("Illustrations") is None
        assert _match_chapter_heading("Contents") is None
        assert _match_chapter_heading("{xxv}") is None


class TestRomanToInt:
    @pytest.mark.parametrize(
        "numeral, expected",
        [
            ("I", 1),
            ("IV", 4),
            ("IX", 9),
            ("XIV", 14),
            ("XLVII", 47),
            ("LXI", 61),
        ],
    )
    def test_basic_conversions(self, numeral: str, expected: int) -> None:
        assert roman_to_int(numeral) == expected


class TestGutenbergParser:
    def test_strip_header_footer(self, tmp_path) -> None:
        html = """
        <html><body>
        <div id="pg-header">Header stuff</div>
        <h2>CHAPTER I. First</h2>
        <p>Some text here.</p>
        <h2>CHAPTER II. Second</h2>
        <p>More text here.</p>
        <div id="pg-footer">Footer stuff</div>
        </body></html>
        """
        path = tmp_path / "test.html"
        path.write_text(html)

        parser = GutenbergParser()
        chapters = parser.parse(path, book_key="test")

        assert len(chapters) == 2
        assert chapters[0]["number"] == 1
        assert chapters[1]["number"] == 2
        assert "Header" not in chapters[0]["text"]
        assert "Footer" not in chapters[-1]["text"]

    def test_drop_cap_unwrap(self, tmp_path) -> None:
        html = """
        <html><body>
        <h2>CHAPTER I</h2>
        <p><span class="letra">T</span>he beginning.</p>
        </body></html>
        """
        path = tmp_path / "test.html"
        path.write_text(html)

        parser = GutenbergParser()
        chapters = parser.parse(path, book_key="test")

        assert len(chapters) == 1
        assert "The beginning" in chapters[0]["text"]

    def test_contiguity(self, tmp_path) -> None:
        html = """
        <html><body>
        <h2>CHAPTER I</h2><p>Text one.</p>
        <h2>CHAPTER III</h2><p>Text three.</p>
        </body></html>
        """
        path = tmp_path / "test.html"
        path.write_text(html)

        parser = GutenbergParser()
        # Should still parse but log a warning about non-contiguous numbers
        chapters = parser.parse(path, book_key="test")
        assert len(chapters) == 2
        assert chapters[0]["number"] == 1
        assert chapters[1]["number"] == 3

    def test_pagenum_markers_stripped(self, tmp_path) -> None:
        # Inline page-number spans must not leak into chapter text.
        html = """
        <html><body>
        <h2>CHAPTER I.</h2>
        <p>They visit no new <span class="pagenum"><a id="page_4">{4}</a></span>comers here.</p>
        </body></html>
        """
        path = tmp_path / "test.html"
        path.write_text(html)

        chapters = GutenbergParser().parse(path, book_key="test")
        assert len(chapters) == 1
        assert "{4}" not in chapters[0]["text"]
        assert "comers" in chapters[0]["text"]

    def test_captioned_and_runtogether_headings(self, tmp_path) -> None:
        # Regression for the P&P edition: captions precede the marker and some
        # headings run "CHAPTER" into the numeral.  All chapters must be found
        # contiguously (this used to drop II and XXVII).
        html = """
        <html><body>
        <h2>CHAPTER I.</h2><p>One.</p>
        <h2>A caption sentence. CHAPTER II.</h2><p>Two.</p>
        <h2>Another caption. CHAPTERIII.</h2><p>Three.</p>
        </body></html>
        """
        path = tmp_path / "test.html"
        path.write_text(html)

        chapters = GutenbergParser().parse(path, book_key="test")
        assert [c["number"] for c in chapters] == [1, 2, 3]
        assert "Two." in chapters[1]["text"]
