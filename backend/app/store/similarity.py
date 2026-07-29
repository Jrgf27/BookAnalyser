"""Cross-book all-pairs cosine similarity via numpy."""

from __future__ import annotations

import numpy as np

from app.models import Chunk, PassagePair
from app.store.base import ChunkStore


def find_cross_book_pairs(
    store: ChunkStore,
    book_id_a: int,
    book_id_b: int,
    top_k: int = 20,
) -> list[PassagePair]:
    """Compute all-pairs cosine similarity between two books' chunks.

    Uses the matrix product  A @ B.T  on L2-normalised embeddings,
    then picks the top-k highest similarities.
    """
    ids_a, mat_a = store.all_embeddings(book_id_a)
    ids_b, mat_b = store.all_embeddings(book_id_b)

    if len(ids_a) == 0 or len(ids_b) == 0:
        return []

    # L2-normalise rows (embeddings should already be unit-length from the
    # API, but normalise defensively)
    norms_a = np.linalg.norm(mat_a, axis=1, keepdims=True)
    norms_b = np.linalg.norm(mat_b, axis=1, keepdims=True)
    norms_a[norms_a == 0] = 1.0
    norms_b[norms_b == 0] = 1.0
    mat_a = mat_a / norms_a
    mat_b = mat_b / norms_b

    # Cosine similarity matrix  (|A| x |B|)
    sim = mat_a @ mat_b.T

    # Flatten and pick top-k indices
    flat = sim.ravel()
    # argpartition is O(n) vs O(n log n) for full sort
    if top_k < len(flat):
        top_flat_idx = np.argpartition(flat, -top_k)[-top_k:]
    else:
        top_flat_idx = np.arange(len(flat))
    # Sort the selected indices by score descending
    top_flat_idx = top_flat_idx[np.argsort(flat[top_flat_idx])[::-1]]

    pairs: list[PassagePair] = []
    for idx in top_flat_idx:
        i = int(idx // len(ids_b))
        j = int(idx % len(ids_b))
        chunk_a_data = store.get(ids_a[i])
        chunk_b_data = store.get(ids_b[j])
        if chunk_a_data is None or chunk_b_data is None:
            continue
        pairs.append(
            PassagePair(
                chunk_a=Chunk(
                    id=chunk_a_data["id"],
                    book_id=chunk_a_data["book_id"],
                    chapter_id=chunk_a_data["chapter_id"],
                    chapter_number=chunk_a_data["chapter_number"],
                    text=chunk_a_data["text"],
                    char_start=chunk_a_data["char_start"],
                    char_end=chunk_a_data["char_end"],
                    token_count=chunk_a_data["token_count"],
                ),
                chunk_b=Chunk(
                    id=chunk_b_data["id"],
                    book_id=chunk_b_data["book_id"],
                    chapter_id=chunk_b_data["chapter_id"],
                    chapter_number=chunk_b_data["chapter_number"],
                    text=chunk_b_data["text"],
                    char_start=chunk_b_data["char_start"],
                    char_end=chunk_b_data["char_end"],
                    token_count=chunk_b_data["token_count"],
                ),
                similarity=float(sim[i, j]),
            )
        )
    return pairs
