"""POST /chat/stream  (Server-Sent Events, session-backed)"""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.models import ChatMessage, ChatRequest
from app.store.sqlite_store import SqliteChunkStore
from app.store.session_store import SessionStore
from app.agent.loop import run_agent_stream
from app.api.deps import get_store, get_session_store, get_settings
from app.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_TITLE_MAX = 60


def _derive_title(message: str) -> str:
    """First line of the opening message, trimmed, as the session title."""
    line = message.strip().splitlines()[0] if message.strip() else "New chat"
    return line[:_TITLE_MAX].rstrip() or "New chat"


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    store: SqliteChunkStore = Depends(get_store),
    session_store: SessionStore = Depends(get_session_store),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a turn over SSE and persist it to the session.

    Events: `session` (first, carries the resolved id), then `token` / `tool`,
    then `done`, or `error`.  History is loaded from the DB — the session, not
    the client, is the source of truth — so the client only sends the new
    message plus the (optional) session id.
    """
    # Resolve or create the session.  Unknown/absent ids start a fresh chat,
    # auto-titled from the opening message.
    session_id = body.session_id
    created = False
    if session_id is None or not session_store.exists(session_id):
        session_id = session_store.create_session(
            title=_derive_title(body.message), book_id=body.book_id
        )
        created = True

    # Prior turns (before this message) drive the agent's memory.
    history = [ChatMessage(**m) for m in session_store.get_history(session_id)]

    # Persist the user turn now and record the latest scope.
    session_store.add_message(session_id, "user", body.message)
    session_store.set_book_id(session_id, body.book_id)

    async def event_source() -> AsyncIterator[str]:
        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield sse({"type": "session", "session_id": session_id, "created": created})
        try:
            async for ev in run_agent_stream(
                body.message,
                store,
                settings,
                book_id=body.book_id,
                history=history,
            ):
                if ev.get("type") == "done":
                    session_store.add_message(session_id, "assistant", ev["answer"])
                yield sse(ev)
        except Exception:  # surface a failure to the client without leaking internals
            # Full detail (type, message, traceback) goes to the server log only;
            # the client gets a generic message so exceptions can't disclose
            # internal paths, dependency errors, or credentials.
            logger.exception("Streaming agent failed")
            yield sse({
                "type": "error",
                "message": "The assistant hit an internal error. Please try again.",
            })

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx) so tokens flush immediately.
            "X-Accel-Buffering": "no",
        },
    )
