"""Chapter + book summaries via one-shot LLM calls.

Each chapter gets a ~2-sentence summary; the book map is built from those.
~108 calls total across both books (47 + 61 chapters).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings
from app.llm.azure import summarize_text
from app.store.sqlite_store import SqliteChunkStore

logger = logging.getLogger(__name__)

CHAPTER_SUMMARY_PROMPT = """\
You are a literary analyst. Summarize the following chapter in 2–3 concise \
sentences. Focus on key plot events, character developments, and thematic \
elements. Do not include chapter numbers or titles in your summary."""

BOOK_SUMMARY_PROMPT = """\
You are a literary analyst. Given the following chapter-by-chapter summaries \
of a novel, write a concise 3–5 sentence overall summary of the book. \
Focus on the main plot arc, central themes, and key characters."""


async def _summarize_one_chapter(
    chapter_id: int,
    chapter_number: int,
    chapter_text: str,
    settings: Settings,
    semaphore: asyncio.Semaphore,
) -> tuple[int, str | None]:
    """Summarize a single chapter, throttled by semaphore.

    Summaries are non-essential (search and chat work without them), so a
    failure — e.g. an Azure content-filter rejection on a chapter's text — must
    not abort the whole ingest.  On error we log and return ``None``.
    """
    async with semaphore:
        # Truncate very long chapters to avoid token limits
        if len(chapter_text) > 15_000:
            chapter_text = chapter_text[:15_000] + "\n\n[...truncated...]"
        try:
            summary = await summarize_text(chapter_text, CHAPTER_SUMMARY_PROMPT, settings)
        except Exception as exc:  # content filter, rate limit exhaustion, etc.
            # Log the human chapter number, not the global row id.
            logger.warning(
                "Chapter %d (id %d) summary skipped: %s",
                chapter_number, chapter_id, exc,
            )
            return chapter_id, None
        return chapter_id, summary


async def summarize_chapters(
    store: SqliteChunkStore,
    book_id: int,
    settings: Settings,
) -> None:
    """Generate summaries for all chapters of a book, then a book-level summary."""
    rows = store.conn.execute(
        "SELECT id, number, text FROM chapters WHERE book_id = ? ORDER BY number",
        (book_id,),
    ).fetchall()

    semaphore = asyncio.Semaphore(settings.summarize_concurrency)

    # Summarize all chapters concurrently (bounded)
    tasks = [
        _summarize_one_chapter(r["id"], r["number"], r["text"], settings, semaphore)
        for r in rows
    ]
    results = await asyncio.gather(*tasks)

    # Write chapter summaries
    for chapter_id, summary in results:
        store.conn.execute(
            "UPDATE chapters SET summary = ? WHERE id = ?",
            (summary, chapter_id),
        )
    store.conn.commit()
    logger.info("Wrote %d chapter summaries for book %d", len(results), book_id)

    # Build book-level summary from chapter summaries
    chapter_summaries = store.conn.execute(
        "SELECT number, summary FROM chapters WHERE book_id = ? ORDER BY number",
        (book_id,),
    ).fetchall()
    combined = "\n".join(
        f"Chapter {r['number']}: {r['summary']}" for r in chapter_summaries if r["summary"]
    )
    book_summary: str | None = None
    if combined.strip():
        try:
            book_summary = await summarize_text(combined, BOOK_SUMMARY_PROMPT, settings)
        except Exception as exc:  # best-effort, like the per-chapter summaries
            logger.warning("Book %d summary skipped: %s", book_id, exc)
    store.conn.execute(
        "UPDATE books SET summary = ? WHERE id = ?", (book_summary, book_id)
    )
    store.conn.commit()
    logger.info("Wrote book summary for book %d", book_id)
