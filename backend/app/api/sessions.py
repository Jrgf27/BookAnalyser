"""CRUD for chat sessions:  /sessions"""

from fastapi import APIRouter, Depends, HTTPException

from app.models import (
    CreateSessionRequest,
    RenameSessionRequest,
    SessionDetail,
    SessionMeta,
)
from app.store.session_store import SessionStore
from app.api.deps import get_session_store

router = APIRouter(prefix="/sessions", tags=["sessions"])

_DEFAULT_TITLE = "New chat"


@router.get("", response_model=list[SessionMeta])
def list_sessions(
    store: SessionStore = Depends(get_session_store),
) -> list[SessionMeta]:
    return [SessionMeta(**row) for row in store.list_sessions()]


@router.post("", response_model=SessionMeta)
def create_session(
    body: CreateSessionRequest,
    store: SessionStore = Depends(get_session_store),
) -> SessionMeta:
    title = (body.title or "").strip() or _DEFAULT_TITLE
    sid = store.create_session(title=title, book_id=body.book_id)
    row = store.get_session(sid)
    assert row is not None
    return SessionMeta(**{k: row[k] for k in ("id", "title", "book_id", "created_at", "updated_at")}, message_count=0)


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
) -> SessionDetail:
    row = store.get_session(session_id)
    if row is None:
        raise HTTPException(404, f"Session {session_id} not found")
    return SessionDetail(**row)


@router.patch("/{session_id}", response_model=SessionMeta)
def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    store: SessionStore = Depends(get_session_store),
) -> SessionMeta:
    if not store.rename_session(session_id, body.title):
        raise HTTPException(404, f"Session {session_id} not found")
    row = store.get_session(session_id)
    assert row is not None
    return SessionMeta(
        **{k: row[k] for k in ("id", "title", "book_id", "created_at", "updated_at")},
        message_count=len(row["messages"]),
    )


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
) -> dict[str, str]:
    if not store.delete_session(session_id):
        raise HTTPException(404, f"Session {session_id} not found")
    return {"status": "deleted"}
