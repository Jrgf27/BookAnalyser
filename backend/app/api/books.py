"""GET /books · GET /books/{id}/outline · POST /books (upload) · DELETE /books/{id}
· GET /books/jobs/{job_id} (ingestion progress)"""

import asyncio
import logging
import re
from typing import Any, Coroutine

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models import BookMeta, Chapter, IngestJobStatus
from app.store.sqlite_store import SqliteChunkStore
from app.ingest.pipeline import ingest_book_html
from app.ingest.jobs import IngestJob, JobRegistry
from app.api.deps import get_store, get_settings, get_jobs
from app.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["books"])

# Keep strong references to in-flight background tasks so they aren't GC'd.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn(coro: "Coroutine[Any, Any, None]") -> None:
    """Schedule a background coroutine, retaining a reference until it finishes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _job_status(job: IngestJob) -> IngestJobStatus:
    return IngestJobStatus(
        id=job.id,
        title=job.title,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        detail=job.detail,
        book_id=job.book_id,
        error=job.error,
    )


async def _run_ingest_job(
    job_id: str,
    jobs: JobRegistry,
    store: SqliteChunkStore,
    settings: Settings,
    *,
    html: str,
    title: str,
    author: str,
) -> None:
    """Background worker: ingest the book and stream progress into the registry."""
    jobs.update(job_id, status="running", stage="parsing", progress=0.02)

    def on_progress(stage: str, progress: float, detail: str) -> None:
        jobs.update(job_id, stage=stage, progress=progress, detail=detail)

    try:
        key = _unique_key(store, _slugify(title))
        book_id = await ingest_book_html(
            store, settings, html=html, key=key, title=title, author=author,
            on_progress=on_progress,
        )
        jobs.update(
            job_id, status="done", stage="done", progress=1.0,
            book_id=book_id, detail="Complete",
        )
    except Exception as exc:  # report failure back to the client
        logger.exception("Ingest job %s failed", job_id)
        jobs.update(job_id, status="error", error=str(exc), detail="Failed")


def _slugify(title: str) -> str:
    """Lowercase alphanumeric key derived from the title.

    Citation markers require ``[a-z0-9]+`` keys, so we strip everything else.
    """
    slug = re.sub(r"[^a-z0-9]+", "", title.lower())
    return slug or "book"


def _unique_key(store: SqliteChunkStore, base: str) -> str:
    """Ensure the key is unique by appending a numeric suffix if needed."""
    key = base
    n = 2
    while store.conn.execute(
        "SELECT 1 FROM books WHERE key = ?", (key,)
    ).fetchone() is not None:
        key = f"{base}{n}"
        n += 1
    return key


@router.get("", response_model=list[BookMeta])
def list_books(store: SqliteChunkStore = Depends(get_store)) -> list[BookMeta]:
    rows = store.conn.execute(
        "SELECT id, title, author, key, word_count, chapter_count, summary FROM books"
    ).fetchall()
    return [
        BookMeta(
            id=r["id"],
            title=r["title"],
            author=r["author"],
            key=r["key"],
            word_count=r["word_count"],
            chapter_count=r["chapter_count"],
            summary=r["summary"],
        )
        for r in rows
    ]


@router.get("/{book_id}/outline", response_model=list[Chapter])
def get_outline(
    book_id: int, store: SqliteChunkStore = Depends(get_store)
) -> list[Chapter]:
    rows = store.conn.execute(
        "SELECT id, book_id, number, title, summary, word_count "
        "FROM chapters WHERE book_id = ? ORDER BY number",
        (book_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(404, f"No chapters found for book_id={book_id}")
    return [
        Chapter(
            id=r["id"],
            book_id=r["book_id"],
            number=r["number"],
            title=r["title"],
            summary=r["summary"],
            word_count=r["word_count"],
        )
        for r in rows
    ]


@router.post("", response_model=IngestJobStatus)
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form(""),
    store: SqliteChunkStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    jobs: JobRegistry = Depends(get_jobs),
) -> IngestJobStatus:
    """Accept an HTML upload and start ingestion in the background.

    Returns immediately with a job id; poll ``GET /books/jobs/{id}`` for
    progress. Only HTML is accepted.
    """
    name = (file.filename or "").lower()
    is_html = name.endswith((".html", ".htm")) or (
        file.content_type or ""
    ).startswith("text/html")
    if not is_html:
        raise HTTPException(400, "Only HTML files (.html/.htm) are supported.")

    title = title.strip()
    if not title:
        raise HTTPException(400, "A title is required.")

    raw = await file.read()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1", errors="replace")

    job = jobs.create(title)
    _spawn(
        _run_ingest_job(
            job.id, jobs, store, settings,
            html=html, title=title, author=author.strip() or "Unknown",
        )
    )
    return _job_status(job)


@router.get("/jobs/{job_id}", response_model=IngestJobStatus)
def get_ingest_job(
    job_id: str, jobs: JobRegistry = Depends(get_jobs)
) -> IngestJobStatus:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"Ingestion job {job_id} not found")
    return _job_status(job)


@router.delete("/{book_id}")
def delete_book(
    book_id: int, store: SqliteChunkStore = Depends(get_store)
) -> dict[str, str]:
    """Remove a book and all its chapters, chunks, and embeddings."""
    exists = store.conn.execute(
        "SELECT 1 FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if exists is None:
        raise HTTPException(404, f"Book {book_id} not found")
    store.drop_book(book_id)
    return {"status": "deleted"}
