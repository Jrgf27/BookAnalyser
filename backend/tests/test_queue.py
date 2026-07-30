"""Tests for the durable ingestion queue: store CRUD, terminal cleanup, resume."""

from __future__ import annotations

import asyncio

import app.api.books as books_api
from app.config import Settings
from app.ingest.jobs import JobRegistry
from app.store.queue_store import IngestQueueStore
from app.store.sqlite_store import SqliteChunkStore


def _store(tmp_path) -> SqliteChunkStore:
    return SqliteChunkStore(tmp_path / "b.db", embedding_dim=8)


def _queue(tmp_path) -> IngestQueueStore:
    return IngestQueueStore(tmp_path / "queue.db")


def _settings() -> Settings:
    return Settings(azure_openai_endpoint="https://x/", azure_openai_api_key="k")


class TestQueueStore:
    def test_add_pending_remove(self, tmp_path) -> None:
        q = _queue(tmp_path)
        assert q.has_pending() is False
        q.add("j1", title="A", author="x", payload=b"<html>a</html>", source="upload")
        q.add("j2", title="B", author="y", payload=b"<html>b</html>", source="seed")

        pending = q.pending()
        assert [p.id for p in pending] == ["j1", "j2"]  # oldest first
        assert pending[0].payload == b"<html>a</html>"
        assert pending[1].source == "seed"
        assert q.has_pending() is True

        q.remove("j1")
        assert [p.id for p in q.pending()] == ["j2"]
        q.close()

    def test_persists_across_reopen(self, tmp_path) -> None:
        q = _queue(tmp_path)
        q.add("j1", title="A", author="x", payload=b"data")
        q.close()

        reopened = IngestQueueStore(tmp_path / "queue.db")
        assert [p.id for p in reopened.pending()] == ["j1"]
        reopened.close()


class TestTerminalCleanup:
    def test_row_removed_on_success(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        jobs = JobRegistry()
        # Don't let enqueue spawn; we run the worker ourselves and await it.
        monkeypatch.setattr(books_api, "_spawn", lambda coro: coro.close())

        async def ok(store, settings, *, html, key, title, author, on_progress=None):
            cur = store.conn.execute(
                "INSERT INTO books (title,author,key,word_count,chapter_count) "
                "VALUES (?,?,?,?,?)",
                (title, author, key, 1, 1),
            )
            store.conn.commit()
            return cur.lastrowid

        monkeypatch.setattr(books_api, "ingest_book_html", ok)
        job = books_api.enqueue_ingestion(
            jobs, queue, store, _settings(),
            title="T", author="A", payload=b"<html></html>",
        )
        assert queue.has_pending() is True  # row written before the run

        asyncio.run(
            books_api._run_ingest_job(
                job.id, jobs, store, _settings(),
                html="<html></html>", title="T", author="A", queue_store=queue,
            )
        )
        assert queue.has_pending() is False  # removed on success
        store.close()
        queue.close()

    def test_row_removed_on_failure(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        jobs = JobRegistry()
        monkeypatch.setattr(books_api, "_spawn", lambda coro: coro.close())

        async def boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(books_api, "ingest_book_html", boom)
        job = books_api.enqueue_ingestion(
            jobs, queue, store, _settings(),
            title="T", author="A", payload=b"<html></html>",
        )
        asyncio.run(
            books_api._run_ingest_job(
                job.id, jobs, store, _settings(),
                html="<html></html>", title="T", author="A", queue_store=queue,
            )
        )
        assert jobs.get(job.id).status == "error"
        assert queue.has_pending() is False  # failed row cleared, won't resume
        store.close()
        queue.close()


class TestResume:
    def test_resumes_pending_row(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        jobs = JobRegistry()
        queue.add("old", title="T", author="A", payload=b"<html></html>")
        spawned = []
        monkeypatch.setattr(books_api, "_spawn", lambda coro: (spawned.append(coro), coro.close()))

        resumed = books_api.resume_pending(jobs, queue, store, _settings())

        assert resumed == 1
        assert len(spawned) == 1
        # A registry job was created under the same id for UI visibility.
        assert jobs.get("old") is not None
        store.close()
        queue.close()

    def test_drops_stale_row_for_existing_book(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        jobs = JobRegistry()
        # Book already ingested (crash happened after commit, before row cleanup).
        store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count,ready) "
            "VALUES ('T','A','t',1,1,1)"
        )
        store.conn.commit()
        queue.add("old", title="T", author="A", payload=b"<html></html>")
        spawned = []
        monkeypatch.setattr(books_api, "_spawn", lambda coro: (spawned.append(coro), coro.close()))

        resumed = books_api.resume_pending(jobs, queue, store, _settings())

        assert resumed == 0
        assert spawned == []
        assert queue.has_pending() is False  # stale row dropped
        store.close()
        queue.close()
