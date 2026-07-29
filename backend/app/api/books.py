"""GET /books  ·  GET /books/{id}/outline"""

from fastapi import APIRouter, Depends, HTTPException

from app.models import BookMeta, Chapter
from app.store.sqlite_store import SqliteChunkStore
from app.api.deps import get_store

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=list[BookMeta])
def list_books(store: SqliteChunkStore = Depends(get_store)) -> list[BookMeta]:
    rows = store.conn.execute(
        "SELECT id, title, author, key, word_count, chapter_count FROM books"
    ).fetchall()
    return [
        BookMeta(
            id=r["id"],
            title=r["title"],
            author=r["author"],
            key=r["key"],
            word_count=r["word_count"],
            chapter_count=r["chapter_count"],
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
