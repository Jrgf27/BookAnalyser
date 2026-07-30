"""Tests for the database export/download endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import get_session_store, get_store
from app.main import app
from app.store.session_store import SessionStore
from app.store.sqlite_store import SqliteChunkStore

_SQLITE_MAGIC = b"SQLite format 3\x00"


def test_export_books_db(tmp_path) -> None:
    store = SqliteChunkStore(tmp_path / "b.db", embedding_dim=8)
    store.conn.execute(
        "INSERT INTO books (title,author,key,word_count,chapter_count,ready) "
        "VALUES ('T','A','t',1,1,1)"
    )
    store.conn.commit()
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)
    try:
        resp = client.get("/export/books.db")
        assert resp.status_code == 200
        # A real SQLite snapshot (not a torn/empty file).
        assert resp.content[:16] == _SQLITE_MAGIC
        disp = resp.headers.get("content-disposition", "")
        assert "attachment" in disp and "books.db" in disp
    finally:
        app.dependency_overrides.clear()
        store.close()


def test_export_sessions_db(tmp_path) -> None:
    store = SessionStore(tmp_path / "s.db")
    store.create_session(title="Chat")
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)
    try:
        resp = client.get("/export/sessions.db")
        assert resp.status_code == 200
        assert resp.content[:16] == _SQLITE_MAGIC
        assert "sessions.db" in resp.headers.get("content-disposition", "")
    finally:
        app.dependency_overrides.clear()
        store.close()
