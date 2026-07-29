"""POST /chat"""

from fastapi import APIRouter, Depends

from app.models import ChatRequest, ChatResponse
from app.store.sqlite_store import SqliteChunkStore
from app.agent.loop import run_agent
from app.api.deps import get_store, get_settings
from app.config import Settings

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    store: SqliteChunkStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    return await run_agent(body.message, store, settings, book_id=body.book_id)
