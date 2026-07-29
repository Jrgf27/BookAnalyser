"""GET /chunks/{id}?window=2"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import Chunk
from app.store.sqlite_store import SqliteChunkStore
from app.api.deps import get_store

router = APIRouter(prefix="/chunks", tags=["chunks"])


@router.get("/{chunk_id}", response_model=dict)
def get_chunk(
    chunk_id: int,
    window: int = Query(0, ge=0, le=5),
    store: SqliteChunkStore = Depends(get_store),
) -> dict:
    """Return a chunk and optionally its surrounding context from the chapter."""
    result = store.get(chunk_id, window=window)
    if result is None:
        raise HTTPException(404, f"Chunk {chunk_id} not found")
    return result
