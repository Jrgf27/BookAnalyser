"""Durable ingestion queue — survives restarts so pending uploads aren't lost.

Backed by a dedicated SQLite file (not ``books.db``) and accessed only from the
asyncio event-loop thread, so it never contends with the chunk-store connection
that ingestion worker threads write to.

A row exists only while its book is pending or in flight; it's removed the moment
the book finishes (success *or* failure). So the table is a true backlog, and any
row still present at startup is unfinished work to resume. Progress/stage detail
is deliberately *not* stored here — that lives in the in-memory ``JobRegistry``
for the live UI; only the coarse, durable facts (payload + status) are persisted,
keeping writes to a handful per book.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_queue (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    author     TEXT NOT NULL,
    payload    BLOB NOT NULL,
    source     TEXT NOT NULL DEFAULT 'upload',
    status     TEXT NOT NULL DEFAULT 'queued',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


@dataclass
class QueuedIngestion:
    id: str
    title: str
    author: str
    payload: bytes
    source: str
    status: str


class IngestQueueStore:
    """Persistent backlog of book ingestions. Single-thread (event-loop) use."""

    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False mirrors the other stores, but by convention this
        # connection is only ever touched on the event-loop thread.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def add(
        self,
        job_id: str,
        *,
        title: str,
        author: str,
        payload: bytes,
        source: str = "upload",
    ) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO ingest_queue "
            "(id, title, author, payload, source, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, title, author, payload, source, now, now),
        )
        self.conn.commit()

    def set_status(self, job_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE ingest_queue SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), job_id),
        )
        self.conn.commit()

    def remove(self, job_id: str) -> None:
        self.conn.execute("DELETE FROM ingest_queue WHERE id = ?", (job_id,))
        self.conn.commit()

    def pending(self) -> list[QueuedIngestion]:
        """All unfinished rows, oldest first (the resume order)."""
        rows = self.conn.execute(
            "SELECT id, title, author, payload, source, status "
            "FROM ingest_queue ORDER BY created_at"
        ).fetchall()
        return [
            QueuedIngestion(
                id=r["id"],
                title=r["title"],
                author=r["author"],
                payload=r["payload"],
                source=r["source"],
                status=r["status"],
            )
            for r in rows
        ]

    def has_pending(self) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM ingest_queue LIMIT 1"
        ).fetchone() is not None

    def close(self) -> None:
        self.conn.close()
