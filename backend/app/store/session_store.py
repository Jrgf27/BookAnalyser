"""SessionStore — persistent chat sessions in a dedicated SQLite file.

Kept separate from ``books.db`` on purpose: the book database is a rebuildable
cache (drop-and-rebuild on ingest), whereas chat sessions are durable user data
that must survive re-ingestion.  Two files, two lifecycles.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT    PRIMARY KEY,
    title       TEXT    NOT NULL,
    book_id     INTEGER,                    -- scope at creation (nullable = all books)
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL,           -- 'user' | 'assistant'
    content     TEXT    NOT NULL,
    created_at  REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""


class SessionStore:
    """SQLite-backed CRUD for chat sessions and their messages."""

    def __init__(self, db_path: Path | str) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # See SqliteChunkStore for the check_same_thread rationale — FastAPI runs
        # sync work in a threadpool and the connection is shared read/write.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")  # enforce ON DELETE CASCADE
        self.conn.executescript(_SCHEMA_SQL)

    # ---- sessions ----

    def create_session(self, *, title: str, book_id: int | None = None) -> str:
        sid = uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO sessions (id, title, book_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, title, book_id, now, now),
        )
        self.conn.commit()
        return sid

    def exists(self, session_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            is not None
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT s.id, s.title, s.book_id, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id)
                       AS message_count
            FROM sessions s
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, title, book_id, created_at, updated_at FROM sessions "
            "WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["messages"] = [
            dict(m)
            for m in self.conn.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        ]
        return data

    def rename_session(self, session_id: str, title: str) -> bool:
        cur = self.conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_book_id(self, session_id: str, book_id: int | None) -> None:
        self.conn.execute(
            "UPDATE sessions SET book_id = ? WHERE id = ?", (book_id, session_id)
        )
        self.conn.commit()

    def delete_session(self, session_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- messages ----

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Prior turns as ``[{'role', 'content'}, ...]`` for agent replay."""
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def add_message(self, session_id: str, role: str, content: str) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, role, content, now),
        )
        # Bump the session so it sorts to the top of the sidebar.
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
