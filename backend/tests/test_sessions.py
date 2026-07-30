"""Tests for session persistence and the session-backed chat endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.chat as chat_api
from app.api.chat import _derive_title
from app.api.deps import get_session_store, get_settings, get_store
from app.main import app
from app.store.session_store import SessionStore


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.db")


class TestSessionStore:
    def test_create_and_get(self, tmp_path) -> None:
        s = _store(tmp_path)
        sid = s.create_session(title="Darcy's proposal", book_id=2)
        row = s.get_session(sid)
        assert row is not None
        assert row["title"] == "Darcy's proposal"
        assert row["book_id"] == 2
        assert row["messages"] == []
        s.close()

    def test_messages_and_history_order(self, tmp_path) -> None:
        s = _store(tmp_path)
        sid = s.create_session(title="t")
        s.add_message(sid, "user", "hi")
        s.add_message(sid, "assistant", "hello [a:1:0]")
        s.add_message(sid, "user", "more")
        hist = s.get_history(sid)
        assert [m["role"] for m in hist] == ["user", "assistant", "user"]
        assert hist[1]["content"] == "hello [a:1:0]"
        # message_count is surfaced in the list view
        assert s.list_sessions()[0]["message_count"] == 3
        s.close()

    def test_rename(self, tmp_path) -> None:
        s = _store(tmp_path)
        sid = s.create_session(title="old")
        assert s.rename_session(sid, "new") is True
        assert s.get_session(sid)["title"] == "new"
        assert s.rename_session("nope", "x") is False
        s.close()

    def test_delete_cascades_messages(self, tmp_path) -> None:
        s = _store(tmp_path)
        sid = s.create_session(title="t")
        s.add_message(sid, "user", "hi")
        assert s.delete_session(sid) is True
        assert s.get_session(sid) is None
        # ON DELETE CASCADE removed the orphaned messages too.
        remaining = s.conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()["n"]
        assert remaining == 0
        s.close()

    def test_list_sorted_by_recency(self, tmp_path) -> None:
        s = _store(tmp_path)
        a = s.create_session(title="A")
        b = s.create_session(title="B")
        # Touch A last → it should sort ahead of B.
        s.add_message(a, "user", "ping")
        ids = [row["id"] for row in s.list_sessions()]
        assert ids[0] == a and ids[1] == b
        s.close()


def test_derive_title() -> None:
    assert _derive_title("  Hello world  ") == "Hello world"
    assert _derive_title("first line\nsecond") == "first line"
    assert len(_derive_title("x" * 200)) <= 60
    assert _derive_title("    ") == "New chat"


class TestChatStreamPersistence:
    def test_stream_creates_and_persists_session(self, tmp_path, monkeypatch) -> None:
        sess = _store(tmp_path)

        async def fake_stream(message, store, settings, *, book_id=None, history=None):
            # History is empty for a brand-new session.
            assert history == []
            yield {"type": "token", "text": "Two books. "}
            yield {"type": "done", "answer": "Two books.", "citations": [], "trace": []}

        monkeypatch.setattr(chat_api, "run_agent_stream", fake_stream)
        app.dependency_overrides[get_store] = lambda: object()
        app.dependency_overrides[get_settings] = lambda: object()
        app.dependency_overrides[get_session_store] = lambda: sess

        # Not used as a context manager → lifespan (which needs Azure env) is skipped.
        client = TestClient(app)
        try:
            resp = client.post("/chat/stream", json={"message": "How many books?"})
            assert resp.status_code == 200
            body = resp.text
            assert '"type": "session"' in body
            assert "Two books." in body

            sessions = sess.list_sessions()
            assert len(sessions) == 1
            detail = sess.get_session(sessions[0]["id"])
            assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
            assert detail["title"] == "How many books?"
        finally:
            app.dependency_overrides.clear()
            sess.close()

    def test_stream_resumes_existing_session(self, tmp_path, monkeypatch) -> None:
        sess = _store(tmp_path)
        sid = sess.create_session(title="Existing")
        sess.add_message(sid, "user", "earlier")
        sess.add_message(sid, "assistant", "reply")

        seen: dict = {}

        async def fake_stream(message, store, settings, *, book_id=None, history=None):
            seen["history_len"] = len(history)
            yield {"type": "done", "answer": "ok", "citations": [], "trace": []}

        monkeypatch.setattr(chat_api, "run_agent_stream", fake_stream)
        app.dependency_overrides[get_store] = lambda: object()
        app.dependency_overrides[get_settings] = lambda: object()
        app.dependency_overrides[get_session_store] = lambda: sess

        client = TestClient(app)
        try:
            resp = client.post(
                "/chat/stream", json={"message": "follow up", "session_id": sid}
            )
            assert resp.status_code == 200
            # Prior two turns were replayed as history.
            assert seen["history_len"] == 2
            detail = sess.get_session(sid)
            # earlier user+assistant, plus new user+assistant = 4
            assert len(detail["messages"]) == 4
            assert detail["title"] == "Existing"  # unchanged on resume
        finally:
            app.dependency_overrides.clear()
            sess.close()
