"""POST /import/books.db · /import/sessions.db — restore a database from an upload.

Full restore: the uploaded snapshot replaces the current database. To make this
safe, we (1) validate the upload is a well-formed SQLite file with the expected
tables *and* passes an integrity check before touching anything, and (2) snapshot
the current live DB first and roll back to it if the overwrite fails — so a
truncated upload or a mid-copy I/O error can never leave the user with corrupted
or half-replaced data.
"""

import logging
import os
import sqlite3
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.store.sqlite_store import SqliteChunkStore
from app.store.session_store import SessionStore
from app.api.deps import get_store, get_session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["import"])

_SQLITE_MAGIC = b"SQLite format 3\x00"


def _save_and_validate(raw: bytes, required: set[str]) -> str:
    """Write the upload to a temp file and verify it's a sound, expected DB.

    Checks, in order: the SQLite magic header, the required tables, and
    ``PRAGMA quick_check`` (catches page-level corruption). Any failure removes
    the temp file and raises, so a bad upload never reaches the live database.
    """
    if raw[:16] != _SQLITE_MAGIC:
        raise HTTPException(400, "That file is not a valid SQLite database.")

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(raw)

    conn = sqlite3.connect(path)
    try:
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            # Page-integrity check: a file can have the right header + tables but
            # be truncated/corrupt. Catch that here, before we overwrite anything.
            check = conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError:
            # Corrupt enough that even introspection fails.
            raise HTTPException(422, "The uploaded database is corrupt or unreadable.")

        missing = required - tables
        if missing:
            raise HTTPException(
                422,
                "This doesn't look like the right database "
                f"(missing: {', '.join(sorted(missing))}).",
            )
        if not check or check[0] != "ok":
            raise HTTPException(422, "The uploaded database is corrupt.")
    except HTTPException:
        os.remove(path)
        raise
    finally:
        conn.close()
    return path


def _snapshot(conn: sqlite3.Connection) -> str:
    """Back up ``conn``'s current database to a temp file; return its path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dest = sqlite3.connect(path)
    try:
        with dest:
            conn.backup(dest)
    finally:
        dest.close()
    return path


def _restore_with_rollback(store, new_db_path: str) -> None:
    """Replace the store's DB with ``new_db_path``, rolling back on failure.

    A snapshot of the current live DB is taken first. If ``restore_from`` throws
    partway through the in-place overwrite (leaving the live DB in an undefined
    state), we re-restore from the snapshot so the user keeps their prior data.
    """
    backup_path = _snapshot(store.conn)
    try:
        store.restore_from(new_db_path)
    except Exception:
        logger.exception("Restore failed; rolling back to the pre-restore snapshot")
        try:
            store.restore_from(backup_path)
        except Exception:
            # Rollback itself failed (e.g. disk error) — nothing more we can do
            # safely; the detail is logged for operator recovery.
            logger.exception("Rollback after a failed restore ALSO failed")
        raise HTTPException(500, "Restore failed; your previous data was kept.")
    finally:
        os.remove(backup_path)


@router.post("/books.db")
async def import_books(
    file: UploadFile = File(...),
    store: SqliteChunkStore = Depends(get_store),
) -> dict[str, str]:
    """Replace the book library with an uploaded books.db snapshot."""
    path = _save_and_validate(await file.read(), {"books", "chapters", "chunks"})
    try:
        _restore_with_rollback(store, path)
    finally:
        os.remove(path)
    return {"status": "restored"}


@router.post("/sessions.db")
async def import_sessions(
    file: UploadFile = File(...),
    store: SessionStore = Depends(get_session_store),
) -> dict[str, str]:
    """Replace the chat history with an uploaded sessions.db snapshot."""
    path = _save_and_validate(await file.read(), {"sessions", "messages"})
    try:
        _restore_with_rollback(store, path)
    finally:
        os.remove(path)
    return {"status": "restored"}
