"""Tool schemas (for the LLM) and dispatch table."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.store.sqlite_store import SqliteChunkStore
from app.llm.azure import get_embedding

# ---- OpenAI function-calling tool schemas ----

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_books",
            "description": "List all available books with metadata (id, title, author, word count, chapter count).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_outline",
            "description": "Get chapter titles and summaries for a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {
                        "type": "integer",
                        "description": "The book ID.",
                    }
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Hybrid semantic + keyword search over text chunks. Returns the most relevant passages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "book_id": {
                        "type": "integer",
                        "description": "Optional: restrict to a single book.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results (default 6).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_context",
            "description": "Retrieve a specific chunk with surrounding context from its chapter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "integer",
                        "description": "The chunk ID to retrieve.",
                    },
                    "window": {
                        "type": "integer",
                        "description": "Number of neighboring chunks to include (default 2).",
                    },
                },
                "required": ["chunk_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_similar_passages",
            "description": "Find thematically similar passages between two books using embedding cosine similarity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id_a": {"type": "integer", "description": "First book ID."},
                    "book_id_b": {"type": "integer", "description": "Second book ID."},
                    "top_k": {
                        "type": "integer",
                        "description": "Number of passage pairs to return (default 10).",
                    },
                },
                "required": ["book_id_a", "book_id_b"],
            },
        },
    },
]


# ---- Dispatch ----

async def dispatch_tool(
    name: str,
    args: dict[str, Any],
    store: SqliteChunkStore,
    settings: Settings,
    *,
    budget: int,
) -> str:
    """Execute a tool call and return a JSON-ish string result, truncated to budget."""
    import json

    result: Any

    if name == "list_books":
        rows = store.conn.execute(
            "SELECT id, title, author, key, word_count, chapter_count FROM books"
        ).fetchall()
        result = [dict(r) for r in rows]

    elif name == "get_outline":
        rows = store.conn.execute(
            "SELECT number, title, summary, word_count FROM chapters "
            "WHERE book_id = ? ORDER BY number",
            (args["book_id"],),
        ).fetchall()
        result = [dict(r) for r in rows]

    elif name == "search":
        query_vec = await get_embedding(args["query"], settings)
        k = args.get("k", 6)
        rows = store.search(
            query_vec,
            query_text=args["query"],
            book_id=args.get("book_id"),
            k=k,
        )
        # Attach book_key for citation
        enriched = []
        for r in rows:
            book_row = store.conn.execute(
                "SELECT key FROM books WHERE id = ?", (r["book_id"],)
            ).fetchone()
            enriched.append({
                "chunk_id": r["id"],
                "book_key": book_row["key"] if book_row else "??",
                "chapter_number": r["chapter_number"],
                "text": r["text"],
            })
        result = enriched

    elif name == "get_context":
        window = args.get("window", 2)
        data = store.get(args["chunk_id"], window=window)
        result = data if data else {"error": "chunk not found"}

    elif name == "find_similar_passages":
        from app.store.similarity import find_cross_book_pairs

        pairs = find_cross_book_pairs(
            store,
            args["book_id_a"],
            args["book_id_b"],
            args.get("top_k", 10),
        )
        result = [p.model_dump() for p in pairs]

    else:
        result = {"error": f"Unknown tool: {name}"}

    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > budget:
        text = text[:budget] + "…[truncated]"
    return text
