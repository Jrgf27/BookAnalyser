"""Tests for book parsing/ingestion, upload/delete endpoints, and summary exposure."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import app.api.books as books_api
import app.ingest.pipeline as pipeline
from app.api.books import _slugify, _unique_key
from app.api.deps import get_jobs, get_queue, get_settings, get_store
from app.config import Settings
from app.ingest.jobs import JobRegistry
from app.ingest.parser import GutenbergParser
from app.main import app
from app.store.queue_store import IngestQueueStore
from app.store.sqlite_store import SqliteChunkStore


def _settings() -> Settings:
    return Settings(azure_openai_endpoint="https://x/", azure_openai_api_key="k")


def _store(tmp_path) -> SqliteChunkStore:
    return SqliteChunkStore(tmp_path / "b.db", embedding_dim=8)


def _queue(tmp_path) -> IngestQueueStore:
    return IngestQueueStore(tmp_path / "queue.db")


# ---- Parser ----


class TestParseHtml:
    def test_extracts_chapters(self) -> None:
        html = "<h2>CHAPTER I.</h2><p>Alpha.</p><h2>CHAPTER II.</h2><p>Beta.</p>"
        chapters = GutenbergParser().parse_html(html, book_key="x")
        assert [c["number"] for c in chapters] == [1, 2]
        assert "Alpha" in chapters[0]["text"]

    def test_fallback_single_chapter(self) -> None:
        # No chapter headings → whole document becomes one chapter.
        html = "<html><body><p>Just some prose with no headings.</p></body></html>"
        chapters = GutenbergParser().parse_html(html, book_key="x")
        assert len(chapters) == 1
        assert chapters[0]["number"] == 1
        assert "prose" in chapters[0]["text"]

    def test_empty_html_yields_nothing(self) -> None:
        assert GutenbergParser().parse_html("<html></html>", book_key="x") == []

    def test_strips_illustration_captions_and_copyright(self) -> None:
        # A figure block (image + caption quote + copyright) sits between two
        # real paragraphs; only the real text should survive.
        html = (
            "<h2>CHAPTER I.</h2>"
            "<p>It is a truth universally acknowledged.</p>"
            '<div class="figcenter"><img src="i.jpg" alt="">'
            '<div class="caption"><p>“He came down to see the place”</p>'
            "<p>[<i>Copyright 1894 by George Allen.</i>]</p></div></div>"
            "<p>However little known the feelings.</p>"
        )
        text = GutenbergParser().parse_html(html, book_key="x")[0]["text"]
        assert "truth universally acknowledged" in text
        assert "However little known" in text
        assert "Copyright" not in text
        assert "came down to see the place" not in text

    def test_preserves_block_quotations(self) -> None:
        # blockquot is used for real letters — it must NOT be stripped.
        html = (
            "<h2>CHAPTER I.</h2>"
            '<div class="blockquot"><p>Dear Sir, I write to inform you.</p></div>'
        )
        text = GutenbergParser().parse_html(html, book_key="x")[0]["text"]
        assert "Dear Sir, I write to inform you." in text

    def test_collapses_source_line_wraps(self) -> None:
        # Gutenberg wraps paragraph text with hard newlines mid-sentence; those
        # must collapse to spaces, while the paragraph break survives.
        html = (
            "<h2>CHAPTER I.</h2>"
            "<p>His character was decided.\nHe was the proudest,\nmost "
            "disagreeable man</p>"
            "<p>Elizabeth Bennet had been\nobliged to sit down.</p>"
        )
        chapters = GutenbergParser().parse_html(html, book_key="x")
        text = chapters[0]["text"]
        assert "decided. He was the proudest, most disagreeable man" in text
        assert "\n\n" in text  # paragraph break preserved
        # No hard-wrap newline survives inside a paragraph.
        for para in text.split("\n\n"):
            assert "\n" not in para


# ---- Slug helpers ----


class TestKeyHelpers:
    def test_slugify_strips_non_alnum(self) -> None:
        assert _slugify("Great Expectations!") == "greatexpectations"
        assert _slugify("A Tale: 2 Cities") == "atale2cities"
        assert _slugify("---") == "book"

    def test_unique_key_appends_suffix(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count) "
            "VALUES ('X','Y','mybook',1,1)"
        )
        store.conn.commit()
        assert _unique_key(store, "mybook") == "mybook2"
        assert _unique_key(store, "fresh") == "fresh"
        store.close()


# ---- Pipeline (LLM calls mocked) ----


class TestIngestPipeline:
    def test_ingests_chapters_and_chunks(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)

        async def fake_embed(texts, settings, **kw):
            return [[0.0] * 8 for _ in texts]

        async def fake_summarize(store, book_id, settings):
            store.conn.execute(
                "UPDATE books SET summary = 'mock' WHERE id = ?", (book_id,)
            )
            store.conn.commit()

        monkeypatch.setattr(pipeline, "get_embeddings_batched", fake_embed)
        monkeypatch.setattr(pipeline, "summarize_chapters", fake_summarize)

        html = (
            "<h2>CHAPTER I.</h2><p>" + ("word " * 300) + "</p>"
            "<h2>CHAPTER II.</h2><p>" + ("more " * 300) + "</p>"
        )
        book_id = asyncio.run(
            pipeline.ingest_book_html(
                store, _settings(), html=html, key="tb", title="Test", author="A"
            )
        )
        book = store.conn.execute(
            "SELECT chapter_count, summary, ready FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        assert book["chapter_count"] == 2
        assert book["summary"] == "mock"
        assert book["ready"] == 1  # flipped ready only after full ingestion
        n_chunks = store.conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE book_id = ?", (book_id,)
        ).fetchone()["n"]
        assert n_chunks > 0
        store.close()

    def test_rejects_empty_html(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        monkeypatch.setattr(pipeline, "get_embeddings_batched", lambda *a, **k: [])
        try:
            asyncio.run(
                pipeline.ingest_book_html(
                    store, _settings(), html="<html></html>",
                    key="x", title="X", author="Y",
                )
            )
            assert False, "expected ValueError"
        except ValueError:
            pass
        store.close()


# ---- Endpoints ----


class TestBookEndpoints:
    def test_get_books_returns_only_ready(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count,summary,ready) "
            "VALUES ('Ready','A','r',1,1,'A short summary',1)"
        )
        # A half-ingested book (ready=0) must not surface in the library.
        store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count,ready) "
            "VALUES ('Partial','A','p',1,1,0)"
        )
        store.conn.commit()
        app.dependency_overrides[get_store] = lambda: store
        client = TestClient(app)
        try:
            resp = client.get("/books")
            assert resp.status_code == 200
            titles = [b["title"] for b in resp.json()]
            assert titles == ["Ready"]
            assert resp.json()[0]["summary"] == "A short summary"
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_upload_rejects_non_html(self, tmp_path) -> None:
        store = _store(tmp_path)
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_settings] = lambda: _settings()
        app.dependency_overrides[get_jobs] = lambda: JobRegistry()
        app.dependency_overrides[get_queue] = lambda: _queue(tmp_path)
        client = TestClient(app)
        try:
            resp = client.post(
                "/books",
                files={"file": ("notes.txt", b"hello", "text/plain")},
                data={"title": "Notes"},
            )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_upload_returns_job_and_status_lookup(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        jobs = JobRegistry()
        # Don't actually run the background coroutine; just close it.
        monkeypatch.setattr(books_api, "_spawn", lambda coro: coro.close())
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_settings] = lambda: _settings()
        app.dependency_overrides[get_jobs] = lambda: jobs
        app.dependency_overrides[get_queue] = lambda: _queue(tmp_path)
        client = TestClient(app)
        try:
            resp = client.post(
                "/books",
                files={"file": ("b.html", b"<html><body><p>hi</p></body></html>", "text/html")},
                data={"title": "My Book", "author": "Me"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "queued"
            assert body["title"] == "My Book"
            job_id = body["id"]

            # The job is queryable via the status endpoint.
            status = client.get(f"/books/jobs/{job_id}")
            assert status.status_code == 200
            assert status.json()["id"] == job_id
            # Unknown job → 404.
            assert client.get("/books/jobs/nope").status_code == 404
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_upload_rejects_duplicate_title_author(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count) "
            "VALUES ('Moby Dick','Herman Melville','mobydick',1,1)"
        )
        store.conn.commit()
        monkeypatch.setattr(books_api, "_spawn", lambda coro: coro.close())
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_settings] = lambda: _settings()
        app.dependency_overrides[get_jobs] = lambda: JobRegistry()
        app.dependency_overrides[get_queue] = lambda: _queue(tmp_path)
        client = TestClient(app)
        try:
            # Same title/author, differing case + whitespace → still a duplicate.
            resp = client.post(
                "/books",
                files={"file": ("b.html", b"<html></html>", "text/html")},
                data={"title": "  moby dick ", "author": "HERMAN MELVILLE"},
            )
            assert resp.status_code == 409

            # A different author is allowed.
            monkeyfree = client.post(
                "/books",
                files={"file": ("b.html", b"<html></html>", "text/html")},
                data={"title": "Moby Dick", "author": "Someone Else"},
            )
            assert monkeyfree.status_code == 200
        finally:
            app.dependency_overrides.clear()
            store.close()

    def test_delete_book(self, tmp_path) -> None:
        store = _store(tmp_path)
        cur = store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count) "
            "VALUES ('T','A','t',1,1)"
        )
        store.conn.commit()
        bid = cur.lastrowid
        app.dependency_overrides[get_store] = lambda: store
        client = TestClient(app)
        try:
            assert client.delete(f"/books/{bid}").status_code == 200
            assert store.conn.execute(
                "SELECT COUNT(*) AS n FROM books"
            ).fetchone()["n"] == 0
            # Deleting again is a 404.
            assert client.delete(f"/books/{bid}").status_code == 404
        finally:
            app.dependency_overrides.clear()
            store.close()
