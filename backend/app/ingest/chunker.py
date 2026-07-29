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

    # Expand paragraphs that exceed the hard ceiling into sentences
    units: list[str] = []
    for para in paragraphs:
        if _count_tokens(para) <= hard_ceiling:
            units.append(para)
        else:
            # Sentence-level fallback
            for sent in _split_sentences(para):
                units.append(sent)

    if not units:
        return []

    chunks: list[dict[str, Any]] = []
    current_units: list[str] = []
    current_tokens = 0

    def _emit(unit_list: list[str]) -> None:
        text = "\n\n".join(unit_list)
        char_start = chapter_text.find(unit_list[0])
        # Find the end position of the last unit
        last_start = chapter_text.find(unit_list[-1], char_start)
        char_end = last_start + len(unit_list[-1])
        chunks.append({
            "text": text,
            "char_start": char_start,
            "char_end": char_end,
            "chapter_number": chapter_number,
            "token_count": _count_tokens(text),
        })

    for unit in units:
        unit_tokens = _count_tokens(unit)

        if current_tokens + unit_tokens > target_tokens and current_units:
            _emit(current_units)

            # Compute overlap: keep trailing units that fit within overlap budget
            overlap_units: list[str] = []
            overlap_tok = 0
            for u in reversed(current_units):
                t = _count_tokens(u)
                if overlap_tok + t > overlap_tokens:
                    break
                overlap_units.insert(0, u)
                overlap_tok += t

            current_units = overlap_units
            current_tokens = overlap_tok

        current_units.append(unit)
        current_tokens += unit_tokens

    # Emit final chunk
    if current_units:
        _emit(current_units)

    return chunks
