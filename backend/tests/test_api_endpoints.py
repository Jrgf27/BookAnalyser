"""HTTP-layer tests for the smaller endpoints — good paths and error paths.

Covers session CRUD 404s, chunk/outline lookups, request validation, and the
/search and /compare routes (with the embedding call stubbed).
"""

from __future__ import annotations

import app.api.search as search_api
from app.api.deps import get_session_store, get_settings, get_store
from app.config import Settings
from app.main import app
from app.store.session_store import SessionStore
from app.store.sqlite_store import SqliteChunkStore

from fastapi.testclient import TestClient


def _settings() -> Settings:
    return Settings(azure_openai_endpoint="https://x/", azure_openai_api_key="k")


def _store(tmp_path) -> SqliteChunkStore:
    return SqliteChunkStore(tmp_path / "api.db", embedding_dim=8)


def _seed_book_with_chunk(store: SqliteChunkStore) -> int:
    store.conn.executescript(
        "INSERT INTO books (id,title,author,key,word_count,chapter_count,ready) "
        "VALUES (1,'A','X','a',1,1,1);"
        "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
        "VALUES (1,1,1,'A1','Elizabeth refused the proposal firmly.',5);"
    )
    store.conn.commit()
    store.upsert(1, 1, [
        {"text": "Elizabeth refused the proposal",
         "char_start": 0, "char_end": 30, "chapter_number": 1, "token_count": 5},
    ], [[1, 0, 0, 0, 0, 0, 0, 0]])
    return store.conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]


# ---- Sessions: error paths ----


class TestSessionEndpointErrors:
    def _client(self, tmp_path) -> tuple[TestClient, SessionStore]:
        sessions = SessionStore(tmp_path / "s.db")
        app.dependency_overrides[get_session_store] = lambda: sessions
        return TestClient(app), sessions

    def test_get_missing_session_404(self, tmp_path) -> None:
        client, sessions = self._client(tmp_path)
        try:
            assert client.get("/sessions/does-not-exist").status_code == 404
        finally:
            app.dependency_overrides.clear()
            sessions.close()

    def test_rename_missing_session_404(self, tmp_path) -> None:
        client, sessions = self._client(tmp_path)
        try:
            resp = client.patch("/sessions/nope", json={"title": "New"})
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()
            sessions.close()

    def test_rename_blank_title_422(self, tmp_path) -> None:
        client, sessions = self._client(tmp_path)
        sid = sessions.create_session(title="Original")
        try:
            resp = client.patch(f"/sessions/{sid}", json={"title": "   "})
            assert resp.status_code == 422
        finally:
            app.dependency_overrides.clear()
            sessions.close()

    def test_delete_missing_session_404(self, tmp_path) -> None:
        client, sessions = self._client(tmp_path)
        try:
            assert client.delete("/sessions/nope").status_code == 404
        finally:
            app.dependency_overrides.clear()
            sessions.close()

    def test_create_and_roundtrip(self, tmp_path) -> None:
        client, sessions = self._client(tmp_path)
        try:
            created = client.post("/sessions", json={"title": "Chat"})
            assert created.status_code == 200
            sid = created.json()["id"]
            got = client.get(f"/sessions/{sid}")
            assert got.status_code == 200
            assert got.json()["title"] == "Chat"
        finally:
            app.dependency_overrides.clear()
            sessions.close()


# ---- Chunks + outline ----


class TestChunkAndOutlineEndpoints:
    def test_get_chunk_ok_and_missing(self, tmp_path) -> None:
        store = _store(tmp_path)
        chunk_id = _seed_book_with_chunk(store)
        app.dependency_overrides[get_store] = lambda: store
        client = TestClient(app)
        try:
            ok = client.get(f"/chunks/{chunk_id}")
            assert ok.status_code == 200
            assert ok.json()["id"] == chunk_id
            assert client.get("/chunks/999999").status_code == 404
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_get_chunk_window_out_of_range_422(self, tmp_path) -> None:
        store = _store(tmp_path)
        chunk_id = _seed_book_with_chunk(store)
        app.dependency_overrides[get_store] = lambda: store
        client = TestClient(app)
        try:
            # window is constrained to 0..5.
            assert client.get(f"/chunks/{chunk_id}?window=6").status_code == 422
            assert client.get(f"/chunks/{chunk_id}?window=-1").status_code == 422
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_outline_missing_book_404(self, tmp_path) -> None:
        store = _store(tmp_path)
        app.dependency_overrides[get_store] = lambda: store
        client = TestClient(app)
        try:
            assert client.get("/books/123/outline").status_code == 404
        finally:
            app.dependency_overrides.clear()
            store.close()


# ---- Chat request validation ----


class TestChatValidation:
    def test_blank_message_rejected_422(self, tmp_path) -> None:
        store = _store(tmp_path)
        sessions = SessionStore(tmp_path / "s.db")
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_session_store] = lambda: sessions
        app.dependency_overrides[get_settings] = lambda: _settings()
        client = TestClient(app)
        try:
            resp = client.post("/chat/stream", json={"message": "   "})
            assert resp.status_code == 422
        finally:
            app.dependency_overrides.clear()
            store.close()
            sessions.close()


# ---- Search + compare ----


class TestSearchAndCompare:
    def test_search_returns_chunks(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        _seed_book_with_chunk(store)

        async def fake_embedding(text, settings):
            return [1, 0, 0, 0, 0, 0, 0, 0]

        monkeypatch.setattr(search_api, "get_embedding", fake_embedding)
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_settings] = lambda: _settings()
        client = TestClient(app)
        try:
            resp = client.post("/search", json={"query": "proposal", "k": 3})
            assert resp.status_code == 200
            body = resp.json()
            assert body and "Elizabeth" in body[0]["text"]
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_compare_returns_pairs(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.conn.executescript(
            "INSERT INTO books (id,title,author,key,word_count,chapter_count,ready) "
            "VALUES (1,'A','X','a',1,1,1),(2,'B','Y','b',1,1,1);"
            "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
            "VALUES (1,1,1,'A1','t',1),(2,2,1,'B1','t',1);"
        )
        store.conn.commit()
        store.upsert(1, 1, [
            {"text": "love and marriage in the countryside",
             "char_start": 0, "char_end": 36, "chapter_number": 1, "token_count": 7},
        ], [[1, 0, 0, 0, 0, 0, 0, 0]])
        store.upsert(2, 2, [
            {"text": "marriage and love among the gentry",
             "char_start": 0, "char_end": 34, "chapter_number": 1, "token_count": 7},
        ], [[0.9, 0.1, 0, 0, 0, 0, 0, 0]])
        app.dependency_overrides[get_store] = lambda: store
        client = TestClient(app)
        try:
            resp = client.post("/compare", json={"book_id_a": 1, "book_id_b": 2, "top_k": 5})
            assert resp.status_code == 200
            pairs = resp.json()
            assert len(pairs) == 1
            assert pairs[0]["similarity"] > 0.5
        finally:
            app.dependency_overrides.clear()
            store.close()
