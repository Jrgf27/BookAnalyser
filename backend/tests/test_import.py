"""Tests for the database import/restore endpoints."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.api.deps import get_session_store, get_store
from app.main import app
from app.store.session_store import SessionStore
from app.store.sqlite_store import SqliteChunkStore


def _books_snapshot(tmp_path) -> bytes:
    src = SqliteChunkStore(tmp_path / "src.db", embedding_dim=8)
    src.conn.execute(
        "INSERT INTO books (title,author,key,word_count,chapter_count,ready) "
        "VALUES ('Imported','A','imp',1,1,1)"
    )
    src.conn.commit()
    snap = tmp_path / "snap.db"
    dst = sqlite3.connect(snap)
    with dst:
        src.conn.backup(dst)
    dst.close()
    src.close()
    return snap.read_bytes()


def test_import_books_rejects_non_sqlite(tmp_path) -> None:
    store = SqliteChunkStore(tmp_path / "live.db", embedding_dim=8)
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)
    try:
        resp = client.post(
            "/import/books.db",
            files={"file": ("x.db", b"not a database", "application/octet-stream")},
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()
        store.close()


def test_import_books_rejects_wrong_schema(tmp_path) -> None:
    # A valid SQLite file, but not a books database.
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE notes (id INTEGER)")
    conn.commit()
    conn.close()

    store = SqliteChunkStore(tmp_path / "live.db", embedding_dim=8)
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)
    try:
        resp = client.post(
            "/import/books.db",
            files={"file": ("other.db", other.read_bytes(), "application/x-sqlite3")},
        )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
        store.close()


def test_import_books_restores(tmp_path) -> None:
    snapshot = _books_snapshot(tmp_path)
    store = SqliteChunkStore(tmp_path / "live.db", embedding_dim=8)
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)
    try:
        resp = client.post(
            "/import/books.db",
            files={"file": ("books.db", snapshot, "application/x-sqlite3")},
        )
        assert resp.status_code == 200
        titles = [r["title"] for r in store.conn.execute("SELECT title FROM books")]
        assert titles == ["Imported"]
    finally:
        app.dependency_overrides.clear()
        store.close()


def test_import_sessions_restores(tmp_path) -> None:
    src = SessionStore(tmp_path / "s_src.db")
    src.create_session(title="Restored chat")
    snap = tmp_path / "s_snap.db"
    dst = sqlite3.connect(snap)
    with dst:
        src.conn.backup(dst)
    dst.close()
    src.close()

    store = SessionStore(tmp_path / "s_live.db")
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)
    try:
        resp = client.post(
            "/import/sessions.db",
            files={"file": ("sessions.db", snap.read_bytes(), "application/x-sqlite3")},
        )
        assert resp.status_code == 200
        assert [s["title"] for s in store.list_sessions()] == ["Restored chat"]
    finally:
        app.dependency_overrides.clear()
        store.close()
