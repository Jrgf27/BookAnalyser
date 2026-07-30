"""Offline ingestion entry point: python -m app.ingest

Parses source HTML → chunks → embeddings → summaries → SQLite.
Idempotent: drops and rebuilds each book.  The heavy lifting lives in
``app.ingest.pipeline`` so the CLI and the upload endpoint share one path.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.ingest.pipeline import ingest_book_html
from app.store.sqlite_store import SqliteChunkStore

logger = logging.getLogger(__name__)

# Book registry: (filename, slug, title, author)
BOOKS = [
    ("little_women.html", "lw", "Little Women", "Louisa May Alcott"),
    ("pride_prejudice.html", "pp", "Pride and Prejudice", "Jane Austen"),
]


async def ingest_file(
    store: SqliteChunkStore,
    settings,
    filename: str,
    key: str,
    title: str,
    author: str,
) -> None:
    logger.info("=== Ingesting %s ===", title)
    html_path = settings.raw_data_dir / filename
    if not html_path.exists():
        logger.error("Source file not found: %s", html_path)
        return
    html = html_path.read_text(encoding="utf-8")
    await ingest_book_html(
        store, settings, html=html, key=key, title=title, author=author
    )
    logger.info("=== Done: %s ===", title)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    store = SqliteChunkStore(
        settings.database_path, embedding_dim=settings.embedding_dimensions
    )

    try:
        for filename, key, title, author in BOOKS:
            await ingest_file(store, settings, filename, key, title, author)
        logger.info("All books ingested successfully.")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
