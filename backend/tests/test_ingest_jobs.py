"""Tests for background ingestion: job registry, progress reporting, and runner."""

from __future__ import annotations

import asyncio
import types

import app.api.books as books_api
import app.ingest.pipeline as pipeline
import app.llm.azure as azure
from app.config import Settings
from app.ingest.jobs import JobRegistry
from app.store.sqlite_store import SqliteChunkStore


def _settings() -> Settings:
    return Settings(azure_openai_endpoint="https://x/", azure_openai_api_key="k")


def _store(tmp_path) -> SqliteChunkStore:
    return SqliteChunkStore(tmp_path / "b.db", embedding_dim=8)


class TestJobRegistry:
    def test_create_get_update(self) -> None:
        reg = JobRegistry()
        job = reg.create("Book")
        assert job.status == "queued"
        reg.update(job.id, status="running", progress=0.5)
        assert reg.get(job.id).progress == 0.5
        assert reg.get(job.id).status == "running"
        assert reg.get("missing") is None
        reg.update("missing", status="x")  # no-op, must not raise

    def test_prunes_old_finished_jobs(self) -> None:
        reg = JobRegistry()
        ids = []
        for i in range(60):
            j = reg.create(f"t{i}")
            reg.update(j.id, status="done")
            ids.append(j.id)
        # Pruning runs on create, so the most-recently-finished job can linger
        # until the next create — hence the +1 tolerance.
        alive = sum(1 for i in ids if reg.get(i) is not None)
        assert alive <= JobRegistry._MAX_FINISHED + 1


class TestPipelineProgress:
    def test_reports_monotonic_stages(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)

        async def fake_embed(texts, settings, *, on_batch=None):
            if on_batch:
                on_batch(len(texts), len(texts))
            return [[0.0] * 8 for _ in texts]

        async def fake_summarize(store, book_id, settings):
            return None

        monkeypatch.setattr(pipeline, "get_embeddings_batched", fake_embed)
        monkeypatch.setattr(pipeline, "summarize_chapters", fake_summarize)

        events: list[tuple[str, float]] = []
        html = "<h2>CHAPTER I.</h2><p>" + ("word " * 200) + "</p>"
        asyncio.run(
            pipeline.ingest_book_html(
                store, _settings(), html=html, key="k", title="T", author="A",
                on_progress=lambda stage, prog, detail: events.append((stage, prog)),
            )
        )
        stages = [e[0] for e in events]
        progresses = [e[1] for e in events]
        assert stages[0] == "parsing"
        assert "embedding" in stages
        assert stages[-1] == "done"
        assert progresses[-1] == 1.0
        assert progresses == sorted(progresses)  # never goes backwards
        store.close()


class TestEmbeddingsBatchCallback:
    def test_on_batch_invoked_per_batch(self, monkeypatch) -> None:
        class FakeEmbeddings:
            async def create(self, **kw):
                n = len(kw["input"])
                return types.SimpleNamespace(
                    data=[
                        types.SimpleNamespace(index=i, embedding=[float(i)])
                        for i in range(n)
                    ]
                )

        class FakeClient:
            embeddings = FakeEmbeddings()

        monkeypatch.setattr(azure, "_get_embed_client", lambda s: FakeClient())

        calls: list[tuple[int, int]] = []
        out = asyncio.run(
            azure.get_embeddings_batched(
                ["a", "b", "c"], _settings(), batch_size=2,
                on_batch=lambda done, total: calls.append((done, total)),
            )
        )
        assert len(out) == 3
        assert calls[0] == (2, 3)
        assert calls[-1] == (3, 3)


class TestIngestRunner:
    def test_success_marks_done_with_book_id(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        jobs = JobRegistry()
        job = jobs.create("T")

        async def fake_ingest(store, settings, *, html, key, title, author, on_progress=None):
            if on_progress:
                on_progress("embedding", 0.5, "halfway")
            cur = store.conn.execute(
                "INSERT INTO books (title,author,key,word_count,chapter_count) "
                "VALUES (?,?,?,?,?)",
                (title, author, key, 1, 1),
            )
            store.conn.commit()
            return cur.lastrowid

        monkeypatch.setattr(books_api, "ingest_book_html", fake_ingest)
        asyncio.run(
            books_api._run_ingest_job(
                job.id, jobs, store, _settings(),
                html="<html/>", title="T", author="A",
            )
        )
        done = jobs.get(job.id)
        assert done.status == "done"
        assert done.book_id is not None
        assert done.progress == 1.0
        store.close()

    def test_failure_marks_error(self, tmp_path, monkeypatch) -> None:
        store = _store(tmp_path)
        jobs = JobRegistry()
        job = jobs.create("T")

        async def fake_ingest(*a, **k):
            raise ValueError("bad html")

        monkeypatch.setattr(books_api, "ingest_book_html", fake_ingest)
        asyncio.run(
            books_api._run_ingest_job(
                job.id, jobs, store, _settings(),
                html="<html/>", title="T", author="A",
            )
        )
        failed = jobs.get(job.id)
        assert failed.status == "error"
        assert "bad html" in failed.error
        store.close()
