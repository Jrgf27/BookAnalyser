"""FTS5 query sanitizer.

FTS5 chokes on raw user input (unmatched quotes, stray operators, punctuation
that looks like column filters).  This module strips the query down to safe
quoted terms joined with implicit AND.

Examples
--------
>>> sanitize_fts_query("What's Elizabeth's opinion?")
'"whats" "elizabeths" "opinion"'
>>> sanitize_fts_query("NOT OR AND")
''
"""

from __future__ import annotations

import re

# FTS5 operators / noise that must never appear as bare tokens
_FTS5_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})

# Strip everything that isn't alphanumeric or whitespace
_NON_ALPHANUM = re.compile(r"[^a-zA-Z0-9\s]")


def sanitize_fts_query(raw: str) -> str:
    """Convert free-form user text into a safe FTS5 query string.

    Strategy:
    1. Remove apostrophes and other punctuation (``what's`` → ``whats``).
    2. Split on whitespace.
    3. Drop FTS5 reserved words.
    4. Wrap each surviving token in double quotes.
    5. Join with spaces (implicit AND in FTS5).

    Returns an empty string if nothing useful remains.
    """
    # Collapse apostrophes first so "Elizabeth's" → "Elizabeths"
    text = raw.replace("'", "").replace("\u2019", "")
    text = _NON_ALPHANUM.sub(" ", text)
    tokens = [
        t for t in text.split() if t.upper() not in _FTS5_OPERATORS and len(t) > 0
    ]
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)
