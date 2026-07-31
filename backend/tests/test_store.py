"""Tests for SqliteChunkStore — uses an in-memory database."""

from __future__ import annotations

import sqlite3

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


class TestHybridSearch:
    """Exercises the RRF-fused vec0 + FTS5 retrieval path end to end."""

    def _seed(self, tmp_path) -> SqliteChunkStore:
        s = SqliteChunkStore(tmp_path / "search.db", embedding_dim=8)
        s.conn.executescript(
            "INSERT INTO books (id,title,author,key,word_count,chapter_count,ready) "
            "VALUES (1,'A','X','a',1,1,1),(2,'B','Y','b',1,1,1);"
            "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
            "VALUES (1,1,1,'A1','t',1),(2,2,1,'B1','t',1);"
        )
        s.conn.commit()
        # Book 1: two chunks on orthogonal axes; Book 2: one chunk.
        s.upsert(1, 1, [
            {"text": "Elizabeth danced with Darcy at the ball",
             "char_start": 0, "char_end": 39, "chapter_number": 1, "token_count": 8},
            {"text": "the ship sailed across the wide ocean",
             "char_start": 40, "char_end": 77, "chapter_number": 1, "token_count": 8},
        ], [[1, 0, 0, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0, 0, 0]])
        s.upsert(2, 2, [
            {"text": "whales swim deep in the cold sea",
             "char_start": 0, "char_end": 32, "chapter_number": 1, "token_count": 7},
        ], [[0, 0, 1, 0, 0, 0, 0, 0]])
        return s

    def test_ranks_semantic_and_keyword_match_first(self, tmp_path) -> None:
        s = self._seed(tmp_path)
        # Vector aligned with the Elizabeth/Darcy chunk *and* keyword overlap.
        results = s.search(
            [1, 0, 0, 0, 0, 0, 0, 0], query_text="Elizabeth Darcy ball", k=3
        )
        assert results, "expected at least one result"
        assert "Elizabeth" in results[0]["text"]
        s.close()

    def test_book_id_filter_excludes_other_books(self, tmp_path) -> None:
        s = self._seed(tmp_path)
        # Even with a query vector pointing at book 2's chunk, the filter wins.
        results = s.search(
            [0, 0, 1, 0, 0, 0, 0, 0], query_text="whales sea", book_id=1, k=5
        )
        assert results
        assert all(r["book_id"] == 1 for r in results)
        s.close()

    def test_vector_only_when_query_text_blank(self, tmp_path) -> None:
        s = self._seed(tmp_path)
        # No usable FTS terms → vector arm alone still returns the nearest chunk.
        results = s.search([0, 1, 0, 0, 0, 0, 0, 0], query_text="", k=2)
        assert results
        assert "ship sailed" in results[0]["text"]
        s.close()

    def test_empty_store_returns_empty(self, tmp_path) -> None:
        s = SqliteChunkStore(tmp_path / "empty.db", embedding_dim=8)
        assert s.search([1, 0, 0, 0, 0, 0, 0, 0], query_text="anything", k=5) == []
        s.close()


class TestReadiness:
    def _store(self, tmp_path) -> SqliteChunkStore:
        return SqliteChunkStore(tmp_path / "r.db", embedding_dim=8)

    def test_mark_ready(self, tmp_path) -> None:
        s = self._store(tmp_path)
        s.conn.execute(
            "INSERT INTO books (id,title,author,key,word_count,chapter_count) "
            "VALUES (1,'T','A','t',1,1)"
        )
        s.conn.commit()
        assert s.conn.execute("SELECT ready FROM books WHERE id=1").fetchone()["ready"] == 0
        s.mark_ready(1)
        assert s.conn.execute("SELECT ready FROM books WHERE id=1").fetchone()["ready"] == 1
        s.close()

    def test_cleanup_incomplete_drops_only_unready(self, tmp_path) -> None:
        s = self._store(tmp_path)
        s.conn.executescript(
            "INSERT INTO books (id,title,author,key,word_count,chapter_count,ready) "
            "VALUES (1,'Done','A','d',1,1,1),(2,'Partial','A','p',1,1,0);"
            "INSERT INTO chapters (id,book_id,number,title,text,word_count) "
            "VALUES (1,2,1,'c','t',1);"
        )
        s.conn.commit()
        removed = s.cleanup_incomplete()
        assert removed == 1
        remaining = [r["title"] for r in s.conn.execute("SELECT title FROM books")]
        assert remaining == ["Done"]
        # The partial book's chapters went too.
        assert s.conn.execute(
            "SELECT COUNT(*) AS n FROM chapters WHERE book_id=2"
        ).fetchone()["n"] == 0
        s.close()

    def test_restore_from_replaces_contents(self, tmp_path) -> None:
        # Source store with a book → snapshot → restore into an empty store.
        src = self._store(tmp_path)
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

        live = SqliteChunkStore(tmp_path / "live.db", embedding_dim=8)
        assert live.conn.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"] == 0
        live.restore_from(snap)
        titles = [r["title"] for r in live.conn.execute("SELECT title FROM books")]
        assert titles == ["Imported"]
        # Vector table still present after restore.
        assert live.conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE name='chunks_vec'"
        ).fetchone()["n"] == 1
        src.close()
        live.close()

    def test_migration_adds_ready_and_marks_existing(self, tmp_path) -> None:
        # Simulate a pre-`ready` database (old schema, no column).
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author TEXT, "
            "key TEXT, word_count INTEGER, chapter_count INTEGER, summary TEXT)"
        )
        conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count) "
            "VALUES ('Old','A','o',1,1)"
        )
        conn.commit()
        conn.close()

        s = SqliteChunkStore(path, embedding_dim=8)
        cols = {r[1] for r in s.conn.execute("PRAGMA table_info(books)")}
        assert "ready" in cols
        # Pre-existing rows are assumed complete → marked ready.
        assert s.conn.execute(
            "SELECT ready FROM books WHERE key='o'"
        ).fetchone()["ready"] == 1
        s.close()
