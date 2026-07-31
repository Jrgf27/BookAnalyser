"""Tests for the streaming agent: message assembly, scope enforcement,
citation validation, and the end-to-end event stream."""

from __future__ import annotations

import asyncio
import json
import types

import app.agent.loop as loop
from app.agent.loop import (
    _build_initial_messages,
    _drop_unsupported_citations,
    run_agent_stream,
)
from app.agent.tools import _collect_chunk_ids, dispatch_tool
from app.config import Settings
from app.models import ChatMessage
from app.store.similarity import _same_region
from app.store.sqlite_store import SqliteChunkStore


def _settings() -> Settings:
    return Settings(azure_openai_endpoint="https://x/", azure_openai_api_key="k")


def _seed_two_books(tmp_path) -> SqliteChunkStore:
    s = SqliteChunkStore(tmp_path / "t.db", embedding_dim=8)
    s.conn.executescript(
        "INSERT INTO books (id,title,author,key,word_count,chapter_count) "
        "VALUES (1,'Book A','X','a',1,1),(2,'Book B','Y','b',1,1);"
        "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
        "VALUES (1,1,1,'A-ch1','ta',1),(2,2,1,'B-ch1','tb',1);"
    )
    s.conn.commit()
    return s


# ---- Fake streaming plumbing (mimics the OpenAI async stream objects) ----


def _chunk(content=None, tool_calls=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])


def _tc_delta(index, tid, name, args):
    return types.SimpleNamespace(
        index=index,
        id=tid,
        function=types.SimpleNamespace(name=name, arguments=args),
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()


class TestScopeEnforcement:
    def test_scope_overrides_get_outline_book(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)
        # Model asked for book 2, but the user scoped to book 1 → scope wins.
        text, _ = asyncio.run(
            dispatch_tool(
                "get_outline", {"book_id": 2}, store, _settings(),
                budget=10_000, scope_book_id=1,
            )
        )
        rows = json.loads(text)
        assert [r["title"] for r in rows] == ["A-ch1"]
        store.close()

    def test_similar_passages_refused_when_scoped(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)
        # Comparison across books is meaningless when pinned to one book.
        text, ids = asyncio.run(
            dispatch_tool(
                "find_similar_passages", {"book_id_a": 1, "book_id_b": 2},
                store, _settings(), budget=10_000, scope_book_id=1,
            )
        )
        assert "error" in json.loads(text)
        assert ids == set()
        store.close()

    def test_similar_passages_allowed_unscoped(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)
        text, _ = asyncio.run(
            dispatch_tool(
                "find_similar_passages", {"book_id_a": 1, "book_id_b": 2},
                store, _settings(), budget=10_000, scope_book_id=None,
            )
        )
        # No chunks/embeddings seeded → empty list, but crucially not an error.
        assert json.loads(text) == []
        store.close()


class TestBuildInitialMessages:
    def test_history_replayed_and_filtered(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)
        history = [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
            ChatMessage(role="tool", content="should be dropped"),
            ChatMessage(role="user", content="   "),  # blank → dropped
        ]
        msgs = _build_initial_messages(
            "who is Darcy?", store, book_id=None, history=history
        )
        assert msgs[0]["role"] == "system"
        # Only the valid user/assistant turns survive, then the current message.
        assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
        assert msgs[-1]["content"] == "who is Darcy?"
        store.close()

    def test_scope_note_added_for_book(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)
        msgs = _build_initial_messages(
            "summarise", store, book_id=1, history=None
        )
        # Second system message pins the scope to Book A.
        assert msgs[1]["role"] == "system"
        assert "Book A" in msgs[1]["content"]
        assert "book_id=1" in msgs[1]["content"]
        store.close()


class TestRunAgentStream:
    def test_tool_then_answer_sequence(self, tmp_path, monkeypatch) -> None:
        store = _seed_two_books(tmp_path)

        calls = {"n": 0}

        async def fake_stream(messages, *, settings, tools=None, temperature=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # First round: model asks for a tool.
                return _FakeStream([
                    _chunk(tool_calls=[_tc_delta(0, "call_1", "list_books", "{}")]),
                ])
            # Second round: model streams a grounded answer with a citation.
            return _FakeStream([
                _chunk(content="There are two books "),
                _chunk(content="[a:1:0]"),
            ])

        async def fake_dispatch(name, args, store, settings, *, budget, scope_book_id=None):
            return json.dumps([{"chunk_id": 0}]), {0}

        monkeypatch.setattr(loop, "chat_completion_stream", fake_stream)
        monkeypatch.setattr(loop, "dispatch_tool", fake_dispatch)

        async def collect():
            return [
                ev async for ev in run_agent_stream(
                    "how many books?", store, _settings(),
                    history=[ChatMessage(role="user", content="hi")],
                )
            ]

        events = asyncio.run(collect())
        types_seq = [e["type"] for e in events]
        assert types_seq == ["tool", "token", "token", "done"]

        done = events[-1]
        # Citation [a:1:0] is supported (chunk 0 surfaced) → kept and parsed.
        assert "[a:1:0]" in done["answer"]
        assert [c["chunk_id"] for c in done["citations"]] == [0]
        assert len(done["trace"]) == 1 and done["trace"][0]["tool"] == "list_books"
        store.close()

    def test_hallucinated_citation_stripped_in_done(self, tmp_path, monkeypatch) -> None:
        store = _seed_two_books(tmp_path)

        async def fake_stream(messages, *, settings, tools=None, temperature=None):
            # No tool calls; model invents a citation for an unseen chunk.
            return _FakeStream([_chunk(content="Made up [a:9:999].")])

        monkeypatch.setattr(loop, "chat_completion_stream", fake_stream)

        async def collect():
            return [
                ev async for ev in run_agent_stream("q", store, _settings())
            ]

        events = asyncio.run(collect())
        done = events[-1]
        assert done["type"] == "done"
        assert "999" not in done["answer"]
        assert done["citations"] == []
        store.close()


class TestDropUnsupportedCitations:
    def test_keeps_supported_citation(self) -> None:
        text = "Elizabeth refuses Darcy [pp:34:12]."
        cleaned, citations = _drop_unsupported_citations(text, {12})
        assert "[pp:34:12]" in cleaned
        assert len(citations) == 1
        assert citations[0].chunk_id == 12

    def test_strips_hallucinated_citation(self) -> None:
        text = "Beth dies peacefully [lw:40:999]."
        cleaned, citations = _drop_unsupported_citations(text, {1, 2, 3})
        assert "999" not in cleaned
        assert citations == []
        # No orphaned double spaces left behind.
        assert "  " not in cleaned

    def test_mixed_citations(self) -> None:
        text = "A [pp:1:5] and B [pp:2:77]."
        cleaned, citations = _drop_unsupported_citations(text, {5})
        assert "[pp:1:5]" in cleaned
        assert "77" not in cleaned
        assert [c.chunk_id for c in citations] == [5]


class TestCollectChunkIds:
    def test_search_ids(self) -> None:
        result = [{"chunk_id": 1}, {"chunk_id": 2}]
        assert _collect_chunk_ids("search", result) == {1, 2}

    def test_get_context_id(self) -> None:
        assert _collect_chunk_ids("get_context", {"id": 7}) == {7}

    def test_similar_pairs_ids(self) -> None:
        result = [{"chunk_a": {"id": 3}, "chunk_b": {"id": 8}}]
        assert _collect_chunk_ids("find_similar_passages", result) == {3, 8}

    def test_metadata_tools_expose_nothing(self) -> None:
        assert _collect_chunk_ids("list_books", [{"id": 1}]) == set()


class TestDispatchTool:
    """Direct dispatch coverage for the tools the agent can call."""

    def _seed_chunked(self, tmp_path) -> SqliteChunkStore:
        s = SqliteChunkStore(tmp_path / "d.db", embedding_dim=8)
        s.conn.executescript(
            "INSERT INTO books (id,title,author,key,word_count,chapter_count,ready) "
            "VALUES (1,'Book A','X','a',1,1,1);"
            "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
            "VALUES (1,1,1,'A-ch1','some chapter text here',4);"
        )
        s.conn.commit()
        s.upsert(1, 1, [
            {"text": "Elizabeth refused the proposal",
             "char_start": 0, "char_end": 30, "chapter_number": 1, "token_count": 5},
        ], [[1, 0, 0, 0, 0, 0, 0, 0]])
        return s

    def test_search_enriches_book_key_and_surfaces_ids(self, tmp_path, monkeypatch) -> None:
        store = self._seed_chunked(tmp_path)

        async def fake_embedding(text, settings):
            return [1, 0, 0, 0, 0, 0, 0, 0]

        import app.agent.tools as tools_mod
        monkeypatch.setattr(tools_mod, "get_embedding", fake_embedding)

        text, ids = asyncio.run(
            dispatch_tool("search", {"query": "proposal"}, store, _settings(), budget=10_000)
        )
        rows = json.loads(text)
        assert rows and rows[0]["book_key"] == "a"
        assert "chunk_id" in rows[0]
        # The surfaced ids let the loop validate any citation of this chunk.
        assert ids == {rows[0]["chunk_id"]}
        store.close()

    def test_get_context_returns_chunk_and_id(self, tmp_path) -> None:
        store = self._seed_chunked(tmp_path)
        chunk_id = store.conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()["id"]
        text, ids = asyncio.run(
            dispatch_tool("get_context", {"chunk_id": chunk_id}, store, _settings(), budget=10_000)
        )
        assert json.loads(text)["id"] == chunk_id
        assert ids == {chunk_id}
        store.close()

    def test_get_context_missing_chunk_reports_error(self, tmp_path) -> None:
        store = self._seed_chunked(tmp_path)
        text, ids = asyncio.run(
            dispatch_tool("get_context", {"chunk_id": 99999}, store, _settings(), budget=10_000)
        )
        assert "error" in json.loads(text)
        assert ids == set()  # nothing surfaced → no citation would validate
        store.close()

    def test_unknown_tool_reports_error(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)
        text, ids = asyncio.run(
            dispatch_tool("frobnicate", {}, store, _settings(), budget=10_000)
        )
        assert "Unknown tool" in json.loads(text)["error"]
        assert ids == set()
        store.close()

    def test_list_books_hides_unready(self, tmp_path) -> None:
        store = _seed_two_books(tmp_path)  # both ready by default? insert lacks ready col
        # Mark one book not-ready and confirm list_books omits it.
        store.conn.execute("UPDATE books SET ready = 1 WHERE id = 1")
        store.conn.execute("UPDATE books SET ready = 0 WHERE id = 2")
        store.conn.commit()
        text, ids = asyncio.run(
            dispatch_tool("list_books", {}, store, _settings(), budget=10_000)
        )
        titles = [b["title"] for b in json.loads(text)]
        assert titles == ["Book A"]
        assert ids == set()  # metadata tool surfaces no chunk ids
        store.close()

    def test_budget_truncates_oversized_result(self, tmp_path, monkeypatch) -> None:
        store = self._seed_chunked(tmp_path)

        async def fake_embedding(text, settings):
            return [1, 0, 0, 0, 0, 0, 0, 0]

        import app.agent.tools as tools_mod
        monkeypatch.setattr(tools_mod, "get_embedding", fake_embedding)

        text, _ = asyncio.run(
            dispatch_tool("search", {"query": "proposal"}, store, _settings(), budget=20)
        )
        assert text.endswith("…[truncated]")
        assert len(text) <= 20 + len("…[truncated]")
        store.close()


class TestSameRegion:
    def test_overlapping_same_chapter(self) -> None:
        a = {"chapter_id": 1, "char_start": 100}
        b = {"chapter_id": 1, "char_start": 300}
        assert _same_region(a, b) is True

    def test_far_apart_same_chapter(self) -> None:
        a = {"chapter_id": 1, "char_start": 100}
        b = {"chapter_id": 1, "char_start": 9000}
        assert _same_region(a, b) is False

    def test_different_chapter(self) -> None:
        a = {"chapter_id": 1, "char_start": 100}
        b = {"chapter_id": 2, "char_start": 120}
        assert _same_region(a, b) is False
