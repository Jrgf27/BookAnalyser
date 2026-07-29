"""FastAPI application — router mounting, DI wiring, lifespan."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.store.sqlite_store import SqliteChunkStore
from app.api import books, chunks, search, chat


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the store on startup, close on shutdown."""
    settings = get_settings()
    store = SqliteChunkStore(settings.database_path, embedding_dim=settings.embedding_dimensions)
    app.state.store = store
    app.state.settings = settings
    yield
    store.close()


app = FastAPI(
    title="Book Assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(books.router)
app.include_router(chunks.router)
app.include_router(search.router)
app.include_router(chat.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
