"""FastAPI dependency helpers."""

from fastapi import Request

from app.config import Settings
from app.store.sqlite_store import SqliteChunkStore


def get_store(request: Request) -> SqliteChunkStore:
    return request.app.state.store  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]
