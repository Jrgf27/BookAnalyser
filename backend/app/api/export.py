"""GET /export/books.db · GET /export/sessions.db — download DB snapshots.

Lets a user carry their data with them. We serve a *consistent snapshot* made
with SQLite's online backup API rather than the raw file, so WAL-pending writes
can't produce a torn/incomplete copy.
"""

import os
import sqlite3
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse

from app.store.sqlite_store import SqliteChunkStore
from app.store.session_store import SessionStore
from app.api.deps import get_store, get_session_store

router = APIRouter(prefix="/export", tags=["export"])


def _snapshot(conn: sqlite3.Connection) -> str:
    """Write a consistent copy of ``conn``'s database to a temp file; return its path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dest = sqlite3.connect(path)
    try:
        with dest:
            conn.backup(dest)  # atomic snapshot, safe under WAL + concurrent reads
    finally:
        dest.close()
    return path


def _download(conn: sqlite3.Connection, filename: str, bg: BackgroundTasks) -> FileResponse:
    path = _snapshot(conn)
    bg.add_task(os.remove, path)  # clean up the temp file after the response is sent
    return FileResponse(path, media_type="application/x-sqlite3", filename=filename)


@router.get("/books.db")
def export_books(
    bg: BackgroundTasks, store: SqliteChunkStore = Depends(get_store)
) -> FileResponse:
    return _download(store.conn, "books.db", bg)


@router.get("/sessions.db")
def export_sessions(
    bg: BackgroundTasks, store: SessionStore = Depends(get_session_store)
) -> FileResponse:
    return _download(store.conn, "sessions.db", bg)
