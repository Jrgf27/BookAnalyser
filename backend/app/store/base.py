"""ChunkStore protocol — the contract that retrieval consumers depend on."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt


@runtime_checkable
class ChunkStore(Protocol):
    """Minimal interface for chunk storage + retrieval."""

    def upsert(
        self,
        book_id: int,
        chapter_id: int,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        """Insert or replace chunks and their embeddings."""
        ...

    def search(
        self,
        query_vec: list[float],
        *,
        query_text: str | None = None,
        book_id: int | None = None,
        k: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid search returning top-k chunks."""
        ...

    def all_embeddings(self, book_id: int) -> tuple[list[int], npt.NDArray[np.float32]]:
        """Return (chunk_ids, embedding_matrix) for a single book."""
        ...

    def get(self, chunk_id: int, window: int = 0) -> dict[str, Any] | None:
        """Retrieve a chunk, optionally expanding context via char offsets."""
        ...
