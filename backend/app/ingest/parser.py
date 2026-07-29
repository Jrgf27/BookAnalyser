"""GutenbergParser — parse Project Gutenberg HTML into chapters.

Handles two different markup conventions (Little Women vs Pride & Prejudice)
by dispatching to per-book extractors.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Roman numeral pattern for chapter detection
_ROMAN = re.compile(
    r"^(CHAPTER\s+)?"
    r"(M{0,3})(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})"
    r"\.?$",
    re.IGNORECASE,
)

ROMAN_VALUES = {
    "I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000,
}


def roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to integer."""
    s = s.upper().strip().rstrip(".")
    total = 0
    prev = 0
    for ch in reversed(s):
        val = ROMAN_VALUES.get(ch, 0)
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total


class GutenbergParser:
    """Parse a Project Gutenberg HTML file into a list of chapter dicts.

    Each chapter dict has keys: number, title, text, word_count.
    """

    def parse(self, path: Path, book_key: str) -> list[dict[str, Any]]:
        logger.info("Parsing %s", path)
        html = path.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "lxml")

        # Strip Gutenberg header/footer
        for sel in ("#pg-header", "#pg-footer", ".pg-header", ".pg-footer"):
            for el in soup.select(sel):
                el.decompose()

        # Unwrap drop-cap spans (some editions use <span class="letra">)
        for span in soup.select("span.letra"):
            span.unwrap()

        # Dispatch to per-book extractor
        chapters = self._extract_chapters(soup, book_key)

        # Assert contiguity: chapter numbers should be 1..N with no gaps
        numbers = [ch["number"] for ch in chapters]
        expected = list(range(1, len(chapters) + 1))
        if numbers != expected:
            logger.warning(
                "Chapter numbers not contiguous for %s: got %s", book_key, numbers
            )

        return chapters

    def _extract_chapters(
        self, soup: BeautifulSoup, book_key: str
    ) -> list[dict[str, Any]]:
        """Generic chapter extraction using heading tags with roman numerals."""
        chapters: list[dict[str, Any]] = []

        # Find all headings that look like chapter markers
        headings = soup.find_all(re.compile(r"^h[1-3]$", re.IGNORECASE))
        chapter_headings: list[tuple[int, Tag, str]] = []

        for h in headings:
            text = h.get_text(" ", strip=True)
            # Try to match "CHAPTER IX" or just "IX" or "Chapter IX. Title"
            m = re.match(
                r"(?:CHAPTER\s+)?([IVXLCDM]+)(?:\.\s*(.*))?$",
                text,
                re.IGNORECASE,
            )
            if m:
                num = roman_to_int(m.group(1))
                title = (m.group(2) or "").strip() or f"Chapter {num}"
                chapter_headings.append((num, h, title))

        if not chapter_headings:
            logger.warning("No chapter headings found for %s", book_key)
            return []

        # Extract text between consecutive chapter headings
        for i, (num, heading, title) in enumerate(chapter_headings):
            # Collect all siblings/elements until the next chapter heading
            parts: list[str] = []
            node = heading.find_next_sibling()

            # Determine the stop element
            stop_heading = (
                chapter_headings[i + 1][1] if i + 1 < len(chapter_headings) else None
            )

            while node is not None:
                if node is stop_heading:
                    break
                if isinstance(node, Tag):
                    parts.append(node.get_text(" ", strip=True))
                node = node.find_next_sibling()

            text = "\n\n".join(p for p in parts if p)
            word_count = len(text.split())

            chapters.append({
                "number": num,
                "title": title,
                "text": text,
                "word_count": word_count,
            })

        return chapters
