"""Tests for startup library seeding (books_api.seed_library)."""

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


def _settings(raw_dir, **over) -> Settings:
    return Settings(
        azure_openai_endpoint="https://x/",
        azure_openai_api_key="k",
        raw_data_dir=raw_dir,
        **over,
    )


def _write_samples(raw_dir, names) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (raw_dir / name).write_text("<h2>CHAPTER I.</h2><p>hello world</p>")


def _stub_spawn(monkeypatch):
    """Capture scheduled coroutines without a running loop; close them cleanly.

    Each enqueued book schedules its own ingest task, so len(spawned) equals the
    number of books seeded.
    """
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()  # avoid "coroutine was never awaited" warnings

    monkeypatch.setattr(books_api, "_spawn", fake_spawn)
    return spawned


class TestSeedLibrary:
    def test_seeds_empty_library(self, tmp_path, monkeypatch) -> None:
        raw = tmp_path / "raw"
        _write_samples(raw, ["little_women.html", "pride_prejudice.html"])
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        spawned = _stub_spawn(monkeypatch)

        started = books_api.seed_library(JobRegistry(), queue, store, _settings(raw))

        assert started == 2
        assert len(spawned) == 2
        # Each seed book is persisted to the durable queue so it survives restart.
        assert len(queue.pending()) == 2
        assert {q.source for q in queue.pending()} == {"seed"}
        store.close()
        queue.close()

    def test_skips_when_library_not_empty(self, tmp_path, monkeypatch) -> None:
        raw = tmp_path / "raw"
        _write_samples(raw, ["little_women.html", "pride_prejudice.html"])
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        store.conn.execute(
            "INSERT INTO books (title,author,key,word_count,chapter_count) "
            "VALUES ('X','Y','x',1,1)"
        )
        store.conn.commit()
        spawned = _stub_spawn(monkeypatch)

        started = books_api.seed_library(JobRegistry(), queue, store, _settings(raw))

        assert started == 0
        assert spawned == []
        store.close()
        queue.close()

    def test_skips_when_queue_has_pending(self, tmp_path, monkeypatch) -> None:
        raw = tmp_path / "raw"
        _write_samples(raw, ["little_women.html", "pride_prejudice.html"])
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        queue.add("j1", title="Pending", author="A", payload=b"<html></html>")
        spawned = _stub_spawn(monkeypatch)

        started = books_api.seed_library(JobRegistry(), queue, store, _settings(raw))

        assert started == 0
        assert spawned == []
        store.close()
        queue.close()

    def test_disabled_flag_skips(self, tmp_path, monkeypatch) -> None:
        raw = tmp_path / "raw"
        _write_samples(raw, ["little_women.html", "pride_prejudice.html"])
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        spawned = _stub_spawn(monkeypatch)

        started = books_api.seed_library(
            JobRegistry(), queue, store, _settings(raw, seed_on_start=False)
        )

        assert started == 0
        assert spawned == []
        store.close()
        queue.close()

    def test_missing_sample_is_skipped(self, tmp_path, monkeypatch) -> None:
        raw = tmp_path / "raw"
        _write_samples(raw, ["little_women.html"])  # only one of the two present
        store = _store(tmp_path)
        queue = _queue(tmp_path)
        spawned = _stub_spawn(monkeypatch)

        started = books_api.seed_library(JobRegistry(), queue, store, _settings(raw))

        assert started == 1
        assert len(spawned) == 1
        store.close()
        queue.close()


class TestIngestSerialization:
    def test_concurrent_ingests_do_not_overlap(self, tmp_path, monkeypatch) -> None:
        """The ingestion lock serializes an upload arriving mid-seed.

        Two _run_ingest_job coroutines started together must run one fully before
        the other begins — never interleaved on the shared DB connection.
        """
        store = _store(tmp_path)
        jobs = JobRegistry()
        order: list[str] = []

        async def fake_ingest(store, settings, *, html, key, title, author, on_progress=None):
            order.append(f"enter-{title}")
            await asyncio.sleep(0.01)  # yield so an unlocked impl would interleave
            order.append(f"exit-{title}")
            cur = store.conn.execute(
                "INSERT INTO books (title,author,key,word_count,chapter_count) "
                "VALUES (?,?,?,?,?)",
                (title, author, key, 1, 1),
            )
            store.conn.commit()
            return cur.lastrowid

        monkeypatch.setattr(books_api, "ingest_book_html", fake_ingest)
        j1 = jobs.create("A")
        j2 = jobs.create("B")

        async def run_both():
            await asyncio.gather(
                books_api._run_ingest_job(
                    j1.id, jobs, store, _settings(tmp_path),
                    html="x", title="A", author="a",
                ),
                books_api._run_ingest_job(
                    j2.id, jobs, store, _settings(tmp_path),
                    html="x", title="B", author="b",
                ),
            )

        asyncio.run(run_both())

        # Each book's enter is immediately followed by its own exit (no interleave).
        assert order in (
            ["enter-A", "exit-A", "enter-B", "exit-B"],
            ["enter-B", "exit-B", "enter-A", "exit-A"],
        )
        assert jobs.get(j1.id).status == "done"
        assert jobs.get(j2.id).status == "done"
        store.close()
