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

# Prefer lxml (fast, handles broken HTML well) but fall back to the stdlib
# html.parser if lxml's C library isn't available (e.g. dev machines without
# libxml2-dev).  Production uses the Docker image which always has lxml.
try:
    import lxml  # noqa: F401
    _BS4_PARSER = "lxml"
except ImportError:
    _BS4_PARSER = "html.parser"
    logger.info("lxml not available, falling back to html.parser")

ROMAN_VALUES = {
    "I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000,
}


# Belt-and-suspenders removal of bracketed illustration/copyright notes that
# survive structural stripping (e.g. "[Copyright 1894 by George Allen.]").
_ARTIFACT_RE = re.compile(r"\[\s*(?:copyright|illustration)[^\]]*\]", re.IGNORECASE)


def _normalize_ws(text: str) -> str:
    """Collapse whitespace runs (incl. the source file's line-wrap newlines)
    into single spaces, so a paragraph reads as continuous prose rather than
    inheriting the HTML's ~70-char hard wraps. Also drops any stray bracketed
    copyright/illustration note left behind."""
    text = _ARTIFACT_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


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


# Matches an explicit "CHAPTER <roman>" marker anywhere in a heading.  The space
# is optional because some headings run the two together ("CHAPTERXXVII."); the
# ``\b`` after the numeral prevents matching a title word that merely starts with
# a roman letter (e.g. "CHAPTERIndex").
_CHAPTER_KW_RE = re.compile(r"\bCHAPTER\s*([IVXLCDM]+)\b\.?\s*(.*)$", re.IGNORECASE)
# Matches a heading that *is* a roman numeral followed by a period, e.g.
# "IX. The Laurence Boy." — the trailing period guards against words that merely
# start with a roman letter ("Illustrations", "Contents").
_ROMAN_LEAD_RE = re.compile(r"^([IVXLCDM]+)\.\s*(.*)$")


def _match_chapter_heading(text: str) -> tuple[int, str] | None:
    """Extract ``(chapter_number, title)`` from a heading, or ``None``.

    Handles two conventions seen in the dataset:

    * *Little Women* — the heading is ``"IX. The Laurence Boy."`` (roman first).
    * *Pride & Prejudice* — some headings carry an illustration caption *before*
      the marker, e.g. ``"I hope Mr. Bingley will like it. CHAPTER II."``.  We
      therefore look for the last ``CHAPTER <roman>`` in the string rather than
      only anchoring at the start.
    """
    kw = list(_CHAPTER_KW_RE.finditer(text))
    if kw:
        m = kw[-1]  # prefer the real marker, past any caption prefix
        return roman_to_int(m.group(1)), m.group(2).strip()

    m = _ROMAN_LEAD_RE.match(text)
    if m:
        return roman_to_int(m.group(1)), m.group(2).strip()

    return None


class GutenbergParser:
    """Parse a Project Gutenberg HTML file into a list of chapter dicts.

    Each chapter dict has keys: number, title, text, word_count.
    """

    def parse(self, path: Path, book_key: str) -> list[dict[str, Any]]:
        logger.info("Parsing %s", path)
        return self.parse_html(path.read_text(encoding="utf-8"), book_key=book_key)

    def parse_html(self, html: str, book_key: str) -> list[dict[str, Any]]:
        """Parse raw HTML into chapters.

        Falls back to a single whole-document chapter when no chapter headings
        are detected, so arbitrary (non-Gutenberg) HTML uploads still ingest.
        """
        soup = BeautifulSoup(html, _BS4_PARSER)

        # Strip Gutenberg header/footer
        for sel in ("#pg-header", "#pg-footer", ".pg-header", ".pg-footer"):
            for el in soup.select(sel):
                el.decompose()

        # Strip inline page-number markers (e.g. <span class="pagenum">{4}</span>),
        # which otherwise leak mid-sentence into chapter text — "no new {4}
        # comers" — polluting chunks, embeddings, and the citation drawer.
        for el in soup.select("span.pagenum, .pagenum"):
            el.decompose()

        # Strip illustration figures and their captions/copyright lines. Both
        # editions wrap illustrations in .figcenter/.figleft containing an <img>
        # and a .caption (P&P: <div class="caption"> with a quote + a
        # "[Copyright 1894 by George Allen.]" line; LW: <span class="caption">).
        # These are figure artifacts, not reading text. Note: `.blockquot` is
        # deliberately NOT stripped — P&P uses it for real letters.
        for el in soup.select(".figcenter, .figleft, .caption, figure, figcaption, img"):
            el.decompose()

        # Unwrap drop-cap spans (some editions use <span class="letra">)
        for span in soup.select("span.letra"):
            span.unwrap()
        # After unwrapping, adjacent text nodes need merging so
        # get_text() doesn't insert spurious spaces (e.g. "T" + "he" → "The")
        soup.smooth()

        # Dispatch to per-book extractor
        chapters = self._extract_chapters(soup, book_key)

        # Fallback for HTML with no recognisable chapter headings: treat the
        # whole document as a single chapter so any upload still ingests.
        if not chapters:
            paragraphs = [
                _normalize_ws(p.get_text(" ", strip=True))
                for p in soup.find_all("p")
            ]
            paragraphs = [p for p in paragraphs if p]
            text = (
                "\n\n".join(paragraphs)
                if paragraphs
                else _normalize_ws(soup.get_text(" ", strip=True))
            )
            if text.strip():
                logger.info("No chapter headings in %s; using single-chapter fallback", book_key)
                chapters = [{
                    "number": 1,
                    "title": "Full text",
                    "text": text,
                    "word_count": len(text.split()),
                }]

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
            match = _match_chapter_heading(text)
            if match:
                num, title = match
                chapter_headings.append((num, h, title or f"Chapter {num}"))

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
                    # Each block becomes one paragraph; collapse its internal
                    # source-line newlines so it reads as continuous prose.
                    parts.append(_normalize_ws(node.get_text(" ", strip=True)))
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
