"""Chunker — split chapter text into overlapping, paragraph-aligned chunks.

Rules:
  • Target ~600 tokens, ~15% overlap with the previous chunk.
  • Pack whole paragraphs; if a paragraph would exceed the hard ceiling,
    fall back to sentence-level splitting.
  • Never cross a chapter boundary (caller passes one chapter at a time).
"""

from __future__ import annotations

import re
from typing import Any

import tiktoken

# Use the encoding by name — model aliases like "gpt-5.1-chat" may not
# be in tiktoken's registry yet.  o200k_base is the GPT-4o / GPT-5 family
# tokenizer; token counts will be close enough for chunking even if the
# exact model BPE differs slightly.
try:
    _ENC = tiktoken.encoding_for_model("gpt-4o")  # resolves to o200k_base
except KeyError:
    _ENC = tiktoken.get_encoding("o200k_base")

# Hard ceiling: never emit a chunk larger than this
_HARD_CEILING_FACTOR = 1.5

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (simple regex-based)."""
    parts = _SENTENCE_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def chunk_chapter(
    chapter_text: str,
    *,
    chapter_number: int,
    target_tokens: int = 600,
    overlap_fraction: float = 0.15,
) -> list[dict[str, Any]]:
    """Split a single chapter's text into overlapping chunks.

    Returns a list of dicts with keys:
        text, char_start, char_end, chapter_number, token_count
    """
    hard_ceiling = int(target_tokens * _HARD_CEILING_FACTOR)
    overlap_tokens = int(target_tokens * overlap_fraction)

    paragraphs = re.split(r"\n\s*\n", chapter_text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Build units with *precomputed* char offsets, located with a forward-only
    # cursor.  A naive chapter_text.find(unit) from position 0 returns the first
    # occurrence, so any repeated paragraph/sentence (refrains, short dialogue
    # like "Yes.") gets the wrong offset — advancing the cursor fixes that and
    # keeps offsets monotonic.  Each unit is {text, start, end, tokens}.
    units: list[dict[str, Any]] = []
    cursor = 0
    for para in paragraphs:
        idx = chapter_text.find(para, cursor)
        if idx == -1:  # whitespace normalisation drift — fall back to anywhere
            idx = chapter_text.find(para)
        p_start = idx if idx != -1 else cursor
        p_end = p_start + len(para)
        cursor = p_end

        if _count_tokens(para) <= hard_ceiling:
            units.append(
                {"text": para, "start": p_start, "end": p_end, "tokens": _count_tokens(para)}
            )
        else:
            # Sentence-level fallback, offsets located within the paragraph span.
            s_cursor = p_start
            for sent in _split_sentences(para):
                sidx = chapter_text.find(sent, s_cursor)
                if sidx == -1 or sidx >= p_end:
                    sidx = chapter_text.find(sent, p_start)
                s_start = sidx if sidx != -1 else s_cursor
                s_end = s_start + len(sent)
                s_cursor = s_end
                units.append(
                    {"text": sent, "start": s_start, "end": s_end, "tokens": _count_tokens(sent)}
                )

    if not units:
        return []

    chunks: list[dict[str, Any]] = []
    current_units: list[dict[str, Any]] = []
    current_tokens = 0

    def _emit(unit_list: list[dict[str, Any]]) -> None:
        text = "\n\n".join(u["text"] for u in unit_list)
        chunks.append({
            "text": text,
            "char_start": unit_list[0]["start"],
            "char_end": unit_list[-1]["end"],
            "chapter_number": chapter_number,
            "token_count": _count_tokens(text),
        })

    for unit in units:
        unit_tokens = unit["tokens"]

        if current_tokens + unit_tokens > target_tokens and current_units:
            _emit(current_units)

            # Compute overlap: keep trailing units that fit within overlap budget
            overlap_units: list[dict[str, Any]] = []
            overlap_tok = 0
            for u in reversed(current_units):
                if overlap_tok + u["tokens"] > overlap_tokens:
                    break
                overlap_units.insert(0, u)
                overlap_tok += u["tokens"]

            current_units = overlap_units
            current_tokens = overlap_tok

        current_units.append(unit)
        current_tokens += unit_tokens

    # Emit final chunk
    if current_units:
        _emit(current_units)

    return chunks
