"""FastAPI application — router mounting, DI wiring, lifespan."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.store.sqlite_store import SqliteChunkStore
from app.store.session_store import SessionStore
from app.ingest.jobs import JobRegistry
from app.api import books, chunks, search, chat, sessions, export, restore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the stores on startup, close on shutdown."""
    settings = get_settings()
    store = SqliteChunkStore(settings.database_path, embedding_dim=settings.embedding_dimensions)
    # Remove any book left half-ingested by a crash/restart before serving.
    removed = store.cleanup_incomplete()
    if removed:
        logging.getLogger(__name__).info("Removed %d incomplete book(s) on startup", removed)
    session_store = SessionStore(settings.sessions_database_path)
    app.state.store = store
    app.state.session_store = session_store
    app.state.ingest_jobs = JobRegistry()
    app.state.settings = settings
    yield
    store.close()
    session_store.close()


app = FastAPI(
    title="Book Assistant",
    version="0.1.0",
    lifespan=lifespan,
)

# No CORS middleware: the browser only ever talks to the frontend origin, and
# both the Vite dev server and the nginx build proxy /api to the backend, so
# requests are same-origin. Add CORSMiddleware only if the API is exposed to a
# browser on a different origin.

app.include_router(books.router)
app.include_router(chunks.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(export.router)
app.include_router(restore.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
