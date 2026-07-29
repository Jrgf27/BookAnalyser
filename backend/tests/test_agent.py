"""Tests for agent-side citation validation and cross-book dedup."""

from __future__ import annotations

import asyncio
import json

from app.agent.loop import _drop_unsupported_citations
from app.agent.tools import _collect_chunk_ids, dispatch_tool
from app.config import get_settings
from app.store.similarity import _same_region
from app.store.sqlite_store import SqliteChunkStore


class TestScopeEnforcement:
    def _store(self, tmp_path) -> SqliteChunkStore:
        s = SqliteChunkStore(tmp_path / "t.db", embedding_dim=8)
        s.conn.executescript(
            "INSERT INTO books (id,title,author,key,word_count,chapter_count) "
            "VALUES (1,'Book A','X','a',1,1),(2,'Book B','Y','b',1,1);"
            "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
            "VALUES (1,1,1,'A-ch1','ta',1),(2,2,1,'B-ch1','tb',1);"
        )
        s.conn.commit()
        return s

    def test_scope_overrides_get_outline_book(self, tmp_path) -> None:
        store = self._store(tmp_path)
        settings = get_settings.__wrapped__() if hasattr(get_settings, "__wrapped__") else None
        # get_settings needs env; build a minimal settings via the store's own.
        from app.config import Settings

        settings = Settings(azure_openai_endpoint="https://x/", azure_openai_api_key="k")
        # Model asked for book 2, but the user scoped to book 1 → scope wins.
        text, _ = asyncio.run(
            dispatch_tool(
                "get_outline", {"book_id": 2}, store, settings, budget=10_000, scope_book_id=1
            )
        )
        rows = json.loads(text)
        assert [r["title"] for r in rows] == ["A-ch1"]
        store.close()


class TestDropUnsupportedCitations:
    def test_keeps_supported_citation(self) -> None:
        text = "Elizabeth refuses Darcy [pp:34:12]."


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
