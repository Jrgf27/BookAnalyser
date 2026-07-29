"""Retrieval evaluation harness.

Measures hit-rate @k against the /search endpoint.
No LLM needed — only the embedding model for query vectorization.

Usage:
    python eval/run.py                    # defaults: k=10, base_url=localhost:8000
    python eval/run.py --k 5 --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import yaml


def load_questions(path: Path) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)


def evaluate(
    questions: list[dict],
    base_url: str,
    k: int,
) -> None:
    client = httpx.Client(base_url=base_url, timeout=30)

    total = 0
    chapter_hits = 0
    chunk_hits = 0

    for q in questions:
        query = q["query"]
        book_key = q.get("book_key")
        expected_chapters = set(q.get("expected_chapters") or [])
        expected_chunks = set(q.get("expected_chunk_ids") or [])

        # Resolve book_id from book_key
        book_id = None
        if book_key:
            books = client.get("/books").json()
            for b in books:
                if b.get("key") == book_key or b["title"].lower().startswith(book_key):
                    book_id = b["id"]
                    break

        # Search
        resp = client.post(
            "/search",
            json={"query": query, "book_id": book_id, "k": k},
        )
        if resp.status_code != 200:
            print(f"  SKIP  {query!r} — HTTP {resp.status_code}")
            continue

        results = resp.json()
        returned_chapters = {r["chapter_number"] for r in results}
        returned_chunks = {r["id"] for r in results}

        # Chapter-level hit rate
        ch_hit = bool(expected_chapters & returned_chapters) if expected_chapters else True
        # Chunk-level hit rate (only if ground truth is populated)
        ck_hit = bool(expected_chunks & returned_chunks) if expected_chunks else True

        total += 1
        if ch_hit:
            chapter_hits += 1
        if ck_hit:
            chunk_hits += 1

        status = "HIT" if ch_hit else "MISS"
        print(f"  {status:4s}  ch={returned_chapters}  q={query!r}")

    print(f"\n{'='*60}")
    print(f"Chapter hit-rate @{k}: {chapter_hits}/{total} = {chapter_hits/max(total,1):.1%}")
    if any(q.get("expected_chunk_ids") for q in questions):
        print(f"Chunk   hit-rate @{k}: {chunk_hits}/{total} = {chunk_hits/max(total,1):.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval evaluation")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--questions", default="eval/questions.yaml")
    args = parser.parse_args()

    questions = load_questions(Path(args.questions))
    print(f"Evaluating {len(questions)} questions at k={args.k}\n")
    evaluate(questions, args.base_url, args.k)


if __name__ == "__main__":
    main()
