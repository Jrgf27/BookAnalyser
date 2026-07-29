"""Tests for SqliteChunkStore — uses an in-memory database."""

from __future__ import annotations

import pytest

from app.store.sqlite_store import SqliteChunkStore
from app.store.fts import sanitize_fts_query


@pytest.fixture
def store(tmp_path) -> SqliteChunkStore:
    db_path = tmp_path / "test.db"
    s = SqliteChunkStore(db_path, embedding_dim=3072)

    # Seed a book + chapter
    s.conn.execute(
        "INSERT INTO books (id, title, author, key, word_count, chapter_count) "
        "VALUES (1, 'Test Book', 'Author', 'tb', 1000, 1)"
    )
    s.conn.execute(
        "INSERT INTO chapters (id, book_id, number, title, text, word_count) "
        "VALUES (1, 1, 1, 'Chapter One', 'Full chapter text here for context expansion.', 7)"
    )
    s.conn.commit()
    return s


class TestSanitizeFtsQuery:
    def test_basic(self) -> None:
        assert sanitize_fts_query("hello world") == '"hello" "world"'

    def test_apostrophes_stripped(self) -> None:
        result = sanitize_fts_query("Elizabeth's opinion")
        assert "Elizabeths" in result
        assert "'" not in result

    def test_operators_removed(self) -> None:
        result = sanitize_fts_query("NOT this AND that OR other")
        assert "NOT" not in result
        assert "AND" not in result
        assert "OR" not in result
        assert '"this"' in result

    def test_empty_result(self) -> None:
        assert sanitize_fts_query("AND OR NOT") == ""

    def test_punctuation_stripped(self) -> None:
        result = sanitize_fts_query("What's the point?!")
        assert "?" not in result
        assert "!" not in result


class TestSqliteChunkStore:
    def test_upsert_and_get(self, store: SqliteChunkStore) -> None:
        chunks = [
            {
                "text": "Hello world",
                "char_start": 0,
                "char_end": 11,
                "chapter_number": 1,
                "token_count": 2,
            }
        ]
        embeddings = [[0.1] * 3072]

        store.upsert(book_id=1, chapter_id=1, chunks=chunks, embeddings=embeddings)

        result = store.get(1)
        assert result is not None
        assert result["text"] == "Hello world"

    def test_get_with_window(self, store: SqliteChunkStore) -> None:
        chunks = [
            {
                "text": "First chunk",
                "char_start": 0,
                "char_end": 11,
                "chapter_number": 1,
                "token_count": 2,
            },
            {
                "text": "Second chunk",
                "char_start": 12,
                "char_end": 24,
                "chapter_number": 1,
                "token_count": 2,
            },
        ]
        embeddings = [[0.1] * 3072, [0.2] * 3072]

        store.upsert(book_id=1, chapter_id=1, chunks=chunks, embeddings=embeddings)

        result = store.get(1, window=1)
        assert result is not None
        assert "context" in result

    def test_get_nonexistent(self, store: SqliteChunkStore) -> None:
        assert store.get(9999) is None

    def test_drop_book(self, store: SqliteChunkStore) -> None:
        chunks = [
            {
                "text": "To be dropped",
                "char_start": 0,
                "char_end": 13,
                "chapter_number": 1,
                "token_count": 3,
            }
        ]
        store.upsert(book_id=1, chapter_id=1, chunks=chunks, embeddings=[[0.1] * 3072])
        store.drop_book(1)

        row = store.conn.execute("SELECT COUNT(*) as c FROM chunks WHERE book_id = 1").fetchone()
        assert row["c"] == 0

    def test_all_embeddings(self, store: SqliteChunkStore) -> None:
        chunks = [
            {
                "text": "Test",
                "char_start": 0,
                "char_end": 4,
                "chapter_number": 1,
                "token_count": 1,
            }
        ]
        store.upsert(book_id=1, chapter_id=1, chunks=chunks, embeddings=[[0.5] * 3072])

        ids, matrix = store.all_embeddings(1)
        assert len(ids) == 1
        assert matrix.shape == (1, 3072)
