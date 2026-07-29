"""Offline ingestion entry point: python -m app.ingest

Parses source HTML → chunks → embeddings → summaries → SQLite.
Idempotent: drops and rebuilds each book.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.config import get_settings
from app.ingest.parser import GutenbergParser
from app.ingest.chunker import chunk_chapter
from app.ingest.summarize import summarize_chapters
from app.llm.azure import get_embeddings_batched
from app.store.sqlite_store import SqliteChunkStore

logger = logging.getLogger(__name__)

# Book registry: (filename, slug, title, author)
BOOKS = [
    ("little_women.html", "lw", "Little Women", "Louisa May Alcott"),
    ("pride_prejudice.html", "pp", "Pride and Prejudice", "Jane Austen"),
]


async def ingest_book(
    store: SqliteChunkStore,
    parser: GutenbergParser,
    filename: str,
    key: str,
    title: str,
    author: str,
) -> None:
    settings = get_settings()
    raw_dir = settings.raw_data_dir

    logger.info("=== Ingesting %s ===", title)

    # Parse HTML
    html_path = raw_dir / filename
    if not html_path.exists():
        logger.error("Source file not found: %s", html_path)
        return

    chapters = parser.parse(html_path, book_key=key)
    logger.info("Parsed %d chapters", len(chapters))

    # Check for existing book and drop
    existing = store.conn.execute(
        "SELECT id FROM books WHERE key = ?", (key,)
    ).fetchone()
    if existing:
        logger.info("Dropping existing data for %s (id=%d)", key, existing["id"])
        store.drop_book(existing["id"])

    # Insert book
    total_words = sum(ch["word_count"] for ch in chapters)
    cur = store.conn.execute(
        "INSERT INTO books (title, author, key, word_count, chapter_count) VALUES (?, ?, ?, ?, ?)",
        (title, author, key, total_words, len(chapters)),
    )
    book_id = cur.lastrowid
    store.conn.commit()

    # Insert chapters and chunk
    all_chunks: list[dict] = []
    chapter_ids: list[int] = []

    for ch in chapters:
        cur = store.conn.execute(
            "INSERT INTO chapters (book_id, number, title, text, word_count) VALUES (?, ?, ?, ?, ?)",
            (book_id, ch["number"], ch["title"], ch["text"], ch["word_count"]),
        )
        chapter_id = cur.lastrowid
        store.conn.commit()
        chapter_ids.append(chapter_id)

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

    logger.info("Created %d chunks", len(all_chunks))

    # Embed all chunks
    texts = [c["text"] for c in all_chunks]
    logger.info("Embedding %d chunks...", len(texts))
    embeddings = await get_embeddings_batched(texts, settings)

    # Group by chapter and upsert
    from itertools import groupby
    from operator import itemgetter

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

    logger.info("Upserted all chunks")

    # Summarize chapters
    logger.info("Summarizing %d chapters...", len(chapters))
    await summarize_chapters(store, book_id, settings)

    logger.info("=== Done: %s ===", title)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    store = SqliteChunkStore(settings.database_path, embedding_dim=settings.embedding_dimensions)
    parser = GutenbergParser()

    try:
        for filename, key, title, author in BOOKS:
            await ingest_book(store, parser, filename, key, title, author)
        logger.info("All books ingested successfully.")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
