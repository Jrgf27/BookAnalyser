"""Single-book ingestion: HTML → chunks → embeddings → summaries → DB.

Drives the book-upload endpoint (`POST /books`): a user uploads an HTML file
and this builds the searchable book from it.

The synchronous, CPU/DB-bound stages (parsing, chunking, SQLite writes) run in a
worker thread via ``asyncio.to_thread`` so a large upload never blocks the event
loop that serves chat streaming and search.  The async LLM calls (embeddings,
summaries) stay on the main loop, keeping the shared Azure client bound to a
single event loop.
"""

from __future__ import annotations

import asyncio
import logging
from itertools import groupby
from typing import Any, Callable

from app.config import Settings
from app.ingest.chunker import chunk_chapter
from app.ingest.parser import GutenbergParser
from app.ingest.summarize import summarize_chapters
from app.llm.azure import get_embeddings_batched
from app.store.sqlite_store import SqliteChunkStore

logger = logging.getLogger(__name__)

# on_progress(stage, progress_0_to_1, human_readable_detail)
ProgressCallback = Callable[[str, float, str], None]


def _prepare_book(
    store: SqliteChunkStore,
    settings: Settings,
    chapters: list[dict[str, Any]],
    key: str,
    title: str,
    author: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Synchronous DB work: drop-and-rebuild the book, insert chapters, chunk.

    Runs in a worker thread.  Returns ``(book_id, all_chunks)``.
    """
    existing = store.conn.execute(
        "SELECT id FROM books WHERE key = ?", (key,)
    ).fetchone()
    if existing:
        logger.info("Replacing existing book %s (id=%d)", key, existing["id"])
        store.drop_book(existing["id"])

    total_words = sum(ch["word_count"] for ch in chapters)
    cur = store.conn.execute(
        "INSERT INTO books (title, author, key, word_count, chapter_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, author, key, total_words, len(chapters)),
    )
    book_id = cur.lastrowid
    store.conn.commit()

    all_chunks: list[dict[str, Any]] = []
    for ch in chapters:
        cur = store.conn.execute(
            "INSERT INTO chapters (book_id, number, title, text, word_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (book_id, ch["number"], ch["title"], ch["text"], ch["word_count"]),
        )
        chapter_id = cur.lastrowid
        store.conn.commit()

        ch_chunks = chunk_chapter(
            ch["text"],
            chapter_number=ch["number"],
            target_tokens=settings.chunk_target_tokens,
            overlap_fraction=settings.chunk_overlap_fraction,
        )
        for c in ch_chunks:
            c["book_id"] = book_id
            c["chapter_id"] = chapter_id
        all_chunks.extend(ch_chunks)

    return book_id, all_chunks


def _drop_by_key(store: SqliteChunkStore, key: str) -> None:
    """Remove any book (and its chapters/chunks/vectors) with this key.

    Used to clean up a partially-ingested book when a later stage fails, so a
    failed upload never leaves a half-built book visible in the library.
    """
    row = store.conn.execute(
        "SELECT id FROM books WHERE key = ?", (key,)
    ).fetchone()
    if row is not None:
        store.drop_book(row["id"])


def _store_embeddings(
    store: SqliteChunkStore,
    book_id: int,
    all_chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> None:
    """Synchronous DB work: upsert chunks + vectors, grouped by chapter."""
    for chapter_id, group in groupby(
        zip(all_chunks, embeddings), key=lambda x: x[0]["chapter_id"]
    ):
        grp = list(group)
        store.upsert(
            book_id=book_id,
            chapter_id=chapter_id,
            chunks=[g[0] for g in grp],
            embeddings=[g[1] for g in grp],
        )


async def ingest_book_html(
    store: SqliteChunkStore,
    settings: Settings,
    *,
    html: str,
    key: str,
    title: str,
    author: str,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Ingest one book from raw HTML and return its new book id.

    Idempotent per key: an existing book with the same ``key`` is dropped and
    rebuilt.  Raises ``ValueError`` if no text could be parsed.  ``on_progress``
    is called at each stage so a background job can report status.
    """
    def report(stage: str, progress: float, detail: str = "") -> None:
        if on_progress is not None:
            on_progress(stage, progress, detail)

    report("parsing", 0.03, "Parsing HTML")
    chapters = await asyncio.to_thread(GutenbergParser().parse_html, html, key)
    if not chapters:
        # Nothing was written yet, so no cleanup needed.
        raise ValueError("No text could be parsed from the provided HTML.")
    logger.info("Parsed %d chapters for %s", len(chapters), title)

    # Everything past here writes to the DB; if any stage fails, drop the
    # partial book so the library never shows a half-ingested title.
    try:
        report("chunking", 0.08, "Chunking text")
        book_id, all_chunks = await asyncio.to_thread(
            _prepare_book, store, settings, chapters, key, title, author
        )
        logger.info("Created %d chunks; embedding…", len(all_chunks))
        report("chunking", 0.1, f"{len(all_chunks)} chunks")

        texts = [c["text"] for c in all_chunks]

        # Embedding is the bulk of the work → per-batch progress, mapped 0.1–0.75.
        def on_batch(done: int, total: int) -> None:
            frac = done / total if total else 1.0
            report("embedding", 0.1 + 0.65 * frac, f"Embedding {done}/{total} chunks")

        embeddings = (
            await get_embeddings_batched(texts, settings, on_batch=on_batch)
            if texts
            else []
        )

        await asyncio.to_thread(
            _store_embeddings, store, book_id, all_chunks, embeddings
        )

        report("summarizing", 0.8, "Summarizing chapters")
        await summarize_chapters(store, book_id, settings)

        # Flip the book to "ready" only now that everything succeeded, so a
        # crash before this point leaves it hidden and sweepable on restart.
        await asyncio.to_thread(store.mark_ready, book_id)
    except Exception:
        logger.exception("Ingest failed for %s; removing partial data", key)
        await asyncio.to_thread(_drop_by_key, store, key)
        raise

    report("done", 1.0, "Complete")
    logger.info("Ingested %s (id=%d)", title, book_id)
    return book_id
