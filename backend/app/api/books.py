"""GET /books · GET /books/{id}/outline · POST /books (upload) · DELETE /books/{id}
· GET /books/jobs/{job_id} (ingestion progress)"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Coroutine

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models import BookMeta, Chapter, IngestJobStatus
from app.store.sqlite_store import SqliteChunkStore
from app.store.queue_store import IngestQueueStore
from app.ingest.pipeline import ingest_book_html
from app.ingest.jobs import IngestJob, JobRegistry
from app.api.deps import get_store, get_settings, get_jobs, get_queue
from app.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["books"])

# Keep strong references to in-flight background tasks so they aren't GC'd.
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# Serializes all book ingestion (seed + uploads). The store uses a single shared
# SQLite connection, which is not safe for concurrent writes from the worker
# threads each ingest hops to. Rather than reject an upload that arrives while a
# seed (or another upload) is running, we let it queue behind this lock and run
# as soon as the current ingest finishes.
_INGEST_LOCK = asyncio.Lock()

# Bundled sample books ingested on first boot (see seed_library). Each entry is
# (filename in raw_data_dir, title, author). A missing file is skipped, so
# removing the samples simply disables seeding for that book.
SEED_MANIFEST: list[tuple[str, str, str]] = [
    ("little_women.html", "Little Women", "Louisa May Alcott"),
    ("pride_prejudice.html", "Pride and Prejudice", "Jane Austen"),
]


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
    queue_store: IngestQueueStore | None = None,
) -> None:
    """Background worker: ingest the book and stream progress into the registry.

    Holds the process-wide ingestion lock for the duration so only one book is
    written to the shared SQLite connection at a time; concurrent requests wait
    their turn instead of racing (and corrupting) the connection.

    When ``queue_store`` is given, this is durable work: its row is flipped to
    ``running`` when the lock is acquired and deleted once the book finishes
    (success or failure), so a restart resumes only genuinely-unfinished books.
    All queue-store access happens here on the event-loop thread — never inside
    the worker thread the ingest hops to — so it never contends with the
    chunk-store connection.
    """
    if _INGEST_LOCK.locked():
        jobs.update(
            job_id, status="queued", stage="waiting",
            detail="Waiting for another ingestion to finish",
        )

    async with _INGEST_LOCK:
        jobs.update(job_id, status="running", stage="parsing", progress=0.02)
        if queue_store is not None:
            queue_store.set_status(job_id, "running")

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
        finally:
            # Terminal either way: drop the durable row so it isn't resumed. A
            # failed book is cleaned up by ingest itself; the user can re-upload.
            if queue_store is not None:
                queue_store.remove(job_id)


def enqueue_ingestion(
    jobs: JobRegistry,
    queue_store: IngestQueueStore,
    store: SqliteChunkStore,
    settings: Settings,
    *,
    title: str,
    author: str,
    payload: bytes,
    source: str = "upload",
) -> IngestJob:
    """Persist a book to the durable queue and schedule its background ingest.

    The registry job and the queue row share one id. The raw HTML ``payload`` is
    stored so the ingest can be resumed verbatim after a restart; it's decoded
    here for the in-process run. Returns immediately — ingestion runs in the
    background and serializes behind the ingest lock.
    """
    job = jobs.create(title)
    queue_store.add(
        job.id, title=title, author=author, payload=payload, source=source
    )
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError:
        html = payload.decode("latin-1", errors="replace")
    _spawn(
        _run_ingest_job(
            job.id, jobs, store, settings,
            html=html, title=title, author=author, queue_store=queue_store,
        )
    )
    return job


def resume_pending(
    jobs: JobRegistry,
    queue_store: IngestQueueStore,
    store: SqliteChunkStore,
    settings: Settings,
) -> int:
    """Re-schedule ingestions left unfinished by a previous run (oldest first).

    Called once at startup, after ``cleanup_incomplete`` has removed any partial
    book. A row whose book already exists is stale (the ingest finished but the
    process died before clearing the row) and is simply dropped. Returns the
    number of books re-queued.
    """
    resumed = 0
    for item in queue_store.pending():
        already = store.conn.execute(
            "SELECT 1 FROM books "
            "WHERE lower(trim(title)) = lower(?) "
            "AND lower(trim(author)) = lower(?) AND ready = 1",
            (item.title, item.author),
        ).fetchone()
        if already is not None:
            queue_store.remove(item.id)
            continue
        try:
            html = item.payload.decode("utf-8")
        except UnicodeDecodeError:
            html = item.payload.decode("latin-1", errors="replace")
        queue_store.set_status(item.id, "queued")
        jobs.create(item.title, job_id=item.id)
        _spawn(
            _run_ingest_job(
                item.id, jobs, store, settings,
                html=html, title=item.title, author=item.author,
                queue_store=queue_store,
            )
        )
        resumed += 1

    if resumed:
        logger.info("Resuming %d pending ingestion(s) from durable queue", resumed)
    return resumed


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
        "SELECT id, title, author, key, word_count, chapter_count, summary "
        "FROM books WHERE ready = 1"
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
    queue: IngestQueueStore = Depends(get_queue),
) -> IngestJobStatus:
    """Accept an HTML upload and start ingestion in the background.

    Returns immediately with a job id; poll ``GET /books/jobs/{id}`` for
    progress. Only HTML is accepted. The book is recorded in the durable queue
    before ingesting, so an upload that's still queued or in flight survives a
    restart and resumes automatically.
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

    author = author.strip() or "Unknown"

    # (title, author) must be unique — reject duplicates up front (case- and
    # whitespace-insensitive) so we never start ingesting a book we'd refuse.
    duplicate = store.conn.execute(
        "SELECT 1 FROM books "
        "WHERE lower(trim(title)) = lower(?) AND lower(trim(author)) = lower(?)",
        (title, author),
    ).fetchone()
    if duplicate is not None:
        raise HTTPException(
            409, f"A book titled '{title}' by {author} already exists."
        )

    raw = await file.read()
    job = enqueue_ingestion(
        jobs, queue, store, settings,
        title=title, author=author, payload=raw, source="upload",
    )
    return _job_status(job)


def seed_library(
    jobs: JobRegistry,
    queue_store: IngestQueueStore,
    store: SqliteChunkStore,
    settings: Settings,
) -> int:
    """Enqueue the bundled sample books on first boot if there's nothing to do.

    Idempotent and non-blocking. Skips entirely when seeding is disabled, any
    book already exists, or the durable queue already has pending work (e.g. the
    resume step re-queued something) — so it never duplicates a library or races
    a resume. Each seed book goes through the durable queue like a normal upload,
    so it shows up with a progress bar and survives a restart. The ingest lock
    serializes the two so they don't race the shared connection. Returns the
    number of books queued.
    """
    if not settings.seed_on_start:
        return 0

    # Nothing to seed if a library already exists, or if work is already pending.
    # cleanup_incomplete() has already run, so a crashed partial won't linger.
    existing = store.conn.execute("SELECT COUNT(*) AS n FROM books").fetchone()
    if existing is not None and existing["n"] > 0:
        return 0
    if queue_store.has_pending():
        return 0

    raw_dir = Path(settings.raw_data_dir)
    started = 0
    for filename, title, author in SEED_MANIFEST:
        path = raw_dir / filename
        if not path.exists():
            logger.warning("Seed book missing, skipping: %s", path)
            continue
        enqueue_ingestion(
            jobs, queue_store, store, settings,
            title=title, author=author, payload=path.read_bytes(), source="seed",
        )
        started += 1

    if started:
        logger.info("Seeding empty library: queued %d ingestion job(s)", started)
    return started


@router.get("/jobs", response_model=list[IngestJobStatus])
def list_ingest_jobs(
    jobs: JobRegistry = Depends(get_jobs),
) -> list[IngestJobStatus]:
    """Active and recently-finished ingestion jobs.

    Lets the client discover ingestions it didn't start — notably the seed jobs
    kicked off by the server at startup — so it can render progress bars and
    report failures for them, not just for its own uploads.
    """
    return [_job_status(j) for j in jobs.list_recent()]


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
