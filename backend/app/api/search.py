"""POST /search  ·  POST /compare"""

from fastapi import APIRouter, Depends

from app.models import Chunk, SearchRequest, CompareRequest, PassagePair
from app.store.sqlite_store import SqliteChunkStore
from app.store.similarity import find_cross_book_pairs
from app.api.deps import get_store, get_settings
from app.config import Settings
from app.llm.azure import get_embedding

router = APIRouter(tags=["search"])


@router.post("/search", response_model=list[Chunk])
async def search_chunks(
    body: SearchRequest,
    store: SqliteChunkStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> list[Chunk]:
    query_vec = await get_embedding(body.query, settings)
    rows = store.search(query_vec, query_text=body.query, book_id=body.book_id, k=body.k)
    return [
        Chunk(
            id=r["id"],
            book_id=r["book_id"],
            chapter_id=r["chapter_id"],
            chapter_number=r["chapter_number"],
            text=r["text"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            token_count=r["token_count"],
        )
        for r in rows
    ]


@router.post("/compare", response_model=list[PassagePair])
def compare_books(
    body: CompareRequest,
    store: SqliteChunkStore = Depends(get_store),
) -> list[PassagePair]:
    pairs = find_cross_book_pairs(store, body.book_id_a, body.book_id_b, body.top_k)
    return pairs
