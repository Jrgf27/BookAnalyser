"""SqliteChunkStore — the single ChunkStore implementation."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import sqlite_vec

from app.store.fts import sanitize_fts_query

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()

# sqlite-vec expects embedding bytes as little-endian float32
def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


class SqliteChunkStore:
    """SQLite-backed store with FTS5 + sqlite-vec hybrid search."""

    def __init__(self, db_path: Path | str, *, embedding_dim: int = 3072) -> None:
        self.embedding_dim = embedding_dim
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False: the connection is created once at app
        # startup (event-loop thread) but FastAPI runs sync endpoints in a
        # worker threadpool, so it must be usable from other threads.  SQLite's
        # default serialized threading mode makes shared use safe for our
        # read-mostly workload.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        # Load sqlite-vec extension
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)

        # Apply schema
        self.conn.executescript(_SCHEMA_SQL)
        self._migrate()

        # Create vec0 table (must happen after extension load)
        self._ensure_vec_table()

    def _migrate(self) -> None:
        """Idempotent schema migrations for pre-existing databases."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(books)")}
        if "ready" not in cols:
            # `ready` flips to 1 only when ingestion fully completes; books mid
            # -ingest (or left partial by a crash) stay 0 and are hidden/cleaned.
            self.conn.execute(
                "ALTER TABLE books ADD COLUMN ready INTEGER NOT NULL DEFAULT 0"
            )
            # Rows that predate the column were fully ingested → mark them ready.
            self.conn.execute("UPDATE books SET ready = 1")
            self.conn.commit()

    def mark_ready(self, book_id: int) -> None:
        """Flag a book as fully ingested (visible to the API/agent)."""
        self.conn.execute("UPDATE books SET ready = 1 WHERE id = ?", (book_id,))
        self.conn.commit()

    def cleanup_incomplete(self) -> int:
        """Drop books never marked ready — partials left by a crash/restart.

        Returns the number removed.  Called on startup so a killed ingest never
        leaves a half-built book in the library.
        """
        ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM books WHERE ready = 0"
            ).fetchall()
        ]
        for book_id in ids:
            self.drop_book(book_id)
        return len(ids)

    def _ensure_vec_table(self) -> None:
        # Check if table already exists
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
        ).fetchone()
        if row is None:
            self.conn.execute(
                f"""
                CREATE VIRTUAL TABLE chunks_vec USING vec0(
                    book_id     INTEGER PARTITION KEY,
                    chapter_number INTEGER,
                    embedding   FLOAT[{self.embedding_dim}] distance_metric=cosine
                )
                """
            )

    # ---- write ----

    def upsert(
        self,
        book_id: int,
        chapter_id: int,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        cur = self.conn.cursor()
        for chunk, emb in zip(chunks, embeddings):
            cur.execute(
                """
                INSERT INTO chunks (book_id, chapter_id, chapter_number,
                                    text, char_start, char_end, token_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    chapter_id,
                    chunk["chapter_number"],
                    chunk["text"],
                    chunk["char_start"],
                    chunk["char_end"],
                    chunk["token_count"],
                ),
            )
            rowid = cur.lastrowid
            cur.execute(
                """
                INSERT INTO chunks_vec (rowid, book_id, chapter_number, embedding)
                VALUES (?, ?, ?, ?)
                """,
                (rowid, book_id, chunk["chapter_number"], _serialize_f32(emb)),
            )
        self.conn.commit()

    def drop_book(self, book_id: int) -> None:
        """Remove all data for a book — used for idempotent rebuild."""
        chunk_ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM chunks WHERE book_id = ?", (book_id,)
            ).fetchall()
        ]
        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            self.conn.execute(
                f"DELETE FROM chunks_vec WHERE rowid IN ({placeholders})", chunk_ids
            )
        self.conn.execute("DELETE FROM chunks WHERE book_id = ?", (book_id,))
        self.conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
        self.conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self.conn.commit()

    # ---- read ----

    def search(
        self,
        query_vec: list[float],
        *,
        query_text: str | None = None,
        book_id: int | None = None,
        k: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid search: vec0 KNN + FTS5 BM25, fused with RRF (k=60)."""
        vec_k = k * 3  # over-retrieve for fusion
        fts_k = k * 3

        # --- vector arm ---
        if book_id is not None:
            vec_rows = self.conn.execute(
                """
                SELECT rowid, distance
                FROM chunks_vec
                WHERE embedding MATCH ? AND k = ? AND book_id = ?
                ORDER BY distance
                """,
                (_serialize_f32(query_vec), vec_k, book_id),
            ).fetchall()
        else:
            vec_rows = self.conn.execute(
                """
                SELECT rowid, distance
                FROM chunks_vec
                WHERE embedding MATCH ? AND k = ?
                ORDER BY distance
                """,
                (_serialize_f32(query_vec), vec_k),
            ).fetchall()

        # --- FTS arm ---
        fts_rows: list[sqlite3.Row] = []
        if query_text:
            safe_query = sanitize_fts_query(query_text)
            if safe_query:
                if book_id is not None:
                    fts_rows = self.conn.execute(
                        """
                        SELECT c.id AS rowid, bm25(chunks_fts) AS score
                        FROM chunks_fts f
                        JOIN chunks c ON c.id = f.rowid
                        WHERE chunks_fts MATCH ? AND c.book_id = ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (safe_query, book_id, fts_k),
                    ).fetchall()
                else:
                    fts_rows = self.conn.execute(
                        """
                        SELECT rowid, bm25(chunks_fts) AS score
                        FROM chunks_fts
                        WHERE chunks_fts MATCH ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (safe_query, fts_k),
                    ).fetchall()

        # --- RRF fusion (k=60) ---
        rrf_k = 60
        scores: dict[int, float] = {}

        for rank, row in enumerate(vec_rows):
            rid = row["rowid"] if isinstance(row, sqlite3.Row) else row[0]
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (rrf_k + rank + 1)

        for rank, row in enumerate(fts_rows):
            rid = row["rowid"] if isinstance(row, sqlite3.Row) else row[0]
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (rrf_k + rank + 1)

        top_ids = sorted(scores, key=scores.__getitem__, reverse=True)[:k]

        if not top_ids:
            return []

        placeholders = ",".join("?" * len(top_ids))
        rows = self.conn.execute(
            f"""
            SELECT id, book_id, chapter_id, chapter_number,
                   text, char_start, char_end, token_count
            FROM chunks
            WHERE id IN ({placeholders})
            """,
            top_ids,
        ).fetchall()

        # Preserve RRF ranking
        row_map = {r["id"]: dict(r) for r in rows}
        return [row_map[rid] for rid in top_ids if rid in row_map]

    def all_embeddings(
        self, book_id: int
    ) -> tuple[list[int], npt.NDArray[np.float32]]:
        """Return (chunk_ids, embedding_matrix) for one book."""
        rows = self.conn.execute(
            "SELECT rowid, embedding FROM chunks_vec WHERE book_id = ?",
            (book_id,),
        ).fetchall()
        ids = [r["rowid"] for r in rows]
        dim = self.embedding_dim
        matrix = np.zeros((len(ids), dim), dtype=np.float32)
        for i, r in enumerate(rows):
            raw = r["embedding"]
            matrix[i] = np.frombuffer(raw, dtype=np.float32)
        return ids, matrix

    def get(self, chunk_id: int, window: int = 0) -> dict[str, Any] | None:
        """Retrieve chunk + optional surrounding context from chapter text."""
        row = self.conn.execute(
            """
            SELECT c.id, c.book_id, c.chapter_id, c.chapter_number,
                   c.text, c.char_start, c.char_end, c.token_count,
                   ch.text AS chapter_text, b.key AS book_key
            FROM chunks c
            JOIN chapters ch ON ch.id = c.chapter_id
            JOIN books b ON b.id = c.book_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None

        result = dict(row)
        if window > 0:
            # Expand context by walking neighbouring chunks in *reading order*
            # (char_start), not by chunk-id arithmetic.  Chunk ids are a global
            # autoincrement, so ±window in id space is not guaranteed to map to
            # adjacent passages; ordering by char_start within the chapter is.
            ordered = self.conn.execute(
                """
                SELECT id, char_start, char_end FROM chunks
                WHERE chapter_id = ?
                ORDER BY char_start
                """,
                (row["chapter_id"],),
            ).fetchall()
            positions = [r["id"] for r in ordered]
            if chunk_id in positions:
                idx = positions.index(chunk_id)
                lo = max(0, idx - window)
                hi = min(len(ordered), idx + window + 1)
                span = ordered[lo:hi]
                ctx_start = min(n["char_start"] for n in span)
                ctx_end = max(n["char_end"] for n in span)
                result["context"] = row["chapter_text"][ctx_start:ctx_end]
                result["context_char_start"] = ctx_start
                result["context_char_end"] = ctx_end

        # Don't send the full chapter text to the client
        result.pop("chapter_text", None)
        return result

    def close(self) -> None:
        self.conn.close()
