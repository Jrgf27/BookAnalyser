"""FastAPI dependency helpers."""

from fastapi import Request

from app.config import Settings
from app.store.sqlite_store import SqliteChunkStore
from app.store.session_store import SessionStore


def get_store(request: Request) -> SqliteChunkStore:
    return request.app.state.store  # type: ignore[no-any-return]


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store  # type: ignore[no-any-return]


def get_jobs(request: Request):
    return request.app.state.ingest_jobs


def get_queue(request: Request):
    return request.app.state.ingest_queue


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]
