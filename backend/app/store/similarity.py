"""Cross-book all-pairs cosine similarity via numpy."""

from __future__ import annotations

import numpy as np

from app.models import Chunk, PassagePair
from app.store.base import ChunkStore


# Two chunks in the same chapter whose text windows start within this many
# characters are treated as the "same passage" for de-duplication purposes
# (chunks target ~600 tokens ≈ a few thousand chars, so this catches the
# overlapping-slice case without merging genuinely distinct scenes).
_SAME_PASSAGE_CHAR_GAP = 1500


def _same_region(a: dict, b: dict) -> bool:
    """True if two chunk dicts point at overlapping text in the same chapter."""
    return (
        a["chapter_id"] == b["chapter_id"]
        and abs(a["char_start"] - b["char_start"]) < _SAME_PASSAGE_CHAR_GAP
    )


def find_cross_book_pairs(
    store: ChunkStore,
    book_id_a: int,
    book_id_b: int,
    top_k: int = 20,
) -> list[PassagePair]:
    """Compute all-pairs cosine similarity between two books' chunks.

    Uses the matrix product  A @ B.T  on L2-normalised embeddings, then walks
    candidates in descending similarity and greedily de-duplicates so the
    returned pairs don't repeat the same passage: a candidate is skipped when
    either of its chunks overlaps a chunk already accepted on the same side.
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

    # Over-select a candidate pool, then dedup down to top_k.  We need more than
    # top_k because near-duplicate slices are pruned during selection.
    flat = sim.ravel()
    pool = min(len(flat), max(top_k * 10, top_k))
    cand_idx = np.argpartition(flat, -pool)[-pool:]
    cand_idx = cand_idx[np.argsort(flat[cand_idx])[::-1]]

    pairs: list[PassagePair] = []
    accepted_a: list[dict] = []
    accepted_b: list[dict] = []

    for idx in cand_idx:
        if len(pairs) >= top_k:
            break
        i = int(idx // len(ids_b))
        j = int(idx % len(ids_b))
        chunk_a_data = store.get(ids_a[i])
        chunk_b_data = store.get(ids_b[j])
        if chunk_a_data is None or chunk_b_data is None:
            continue

        # Skip redundant slices of an already-selected passage on either side.
        if any(_same_region(chunk_a_data, a) for a in accepted_a) or any(
            _same_region(chunk_b_data, b) for b in accepted_b
        ):
            continue
        accepted_a.append(chunk_a_data)
        accepted_b.append(chunk_b_data)

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
