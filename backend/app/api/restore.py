"""POST /import/books.db · /import/sessions.db — restore a database from an upload.

Full restore: the uploaded snapshot replaces the current database. We validate
that the file is SQLite and has the expected tables before overwriting anything.
"""

import os
import sqlite3
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.store.sqlite_store import SqliteChunkStore
from app.store.session_store import SessionStore
from app.api.deps import get_store, get_session_store

router = APIRouter(prefix="/import", tags=["import"])

_SQLITE_MAGIC = b"SQLite format 3\x00"


def _save_and_validate(raw: bytes, required: set[str]) -> str:
    """Write the upload to a temp file and verify it's the expected DB shape."""
    if raw[:16] != _SQLITE_MAGIC:
        raise HTTPException(400, "That file is not a valid SQLite database.")

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(raw)

    conn = sqlite3.connect(path)
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    missing = required - tables
    if missing:
        os.remove(path)
        raise HTTPException(
            422,
            "This doesn't look like the right database "
            f"(missing: {', '.join(sorted(missing))}).",
        )
    return path


@router.post("/books.db")
async def import_books(
    file: UploadFile = File(...),
    store: SqliteChunkStore = Depends(get_store),
) -> dict[str, str]:
    """Replace the book library with an uploaded books.db snapshot."""
    path = _save_and_validate(await file.read(), {"books", "chapters", "chunks"})
    try:
        store.restore_from(path)
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
        store.restore_from(path)
    finally:
        os.remove(path)
    return {"status": "restored"}
