"""Tests for the Gutenberg HTML parser."""

from __future__ import annotations

import pytest

from app.ingest.parser import GutenbergParser, roman_to_int


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
