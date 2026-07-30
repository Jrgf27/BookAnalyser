"""Evaluation harness.

Two modes:

* ``retrieval`` (default) — hit-rate @k against ``/search``.  Needs only the
  embedding model, so it's cheap and deterministic.
* ``faithfulness`` — end-to-end check against ``/chat/stream``.  For each
  question it asks the agent (consuming the SSE stream), parses the inline
  citations, and verifies that every cited chunk actually exists, resolves to
  the expected book, and lands in an expected chapter.  This catches
  hallucinated or misgrounded citations that a pure retrieval metric can't see.

Usage:
    python eval/run.py                                  # retrieval, k=10
    python eval/run.py --k 5
    python eval/run.py --mode faithfulness              # end-to-end, needs LLM
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import httpx
import yaml

# Inline citation markers like [pp:34:1207] (keys are alphanumeric slugs)
_CITE_RE = re.compile(r"\[([a-z0-9]+):(\d+):(\d+)\]")


def _stream_answer(
    client: httpx.Client, message: str, book_id: int | None
) -> tuple[str, str | None]:
    """POST to /chat/stream and reassemble the final answer from SSE events.

    Returns ``(answer, session_id)``; the caller deletes the throwaway session
    so evaluation runs don't accumulate junk conversations.
    """
    answer = ""
    session_id: str | None = None
    with client.stream(
        "POST", "/chat/stream", json={"message": message, "book_id": book_id}
    ) as resp:
        if resp.status_code != 200:
            resp.read()
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp
            )
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            kind = ev.get("type")
            if kind == "session":
                session_id = ev.get("session_id")
            elif kind == "done":
                # The terminal event carries the citation-validated answer.
                answer = ev.get("answer", "")
            elif kind == "error":
                raise RuntimeError(ev.get("message", "stream error"))
    return answer, session_id


def load_questions(path: Path) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)


def _resolve_book_id(client: httpx.Client, book_key: str | None) -> int | None:
    if not book_key:
        return None
    for b in client.get("/books").json():
        if b.get("key") == book_key or b["title"].lower().startswith(book_key):
            return b["id"]
    return None


# --------------------------------------------------------------------------- #
# Retrieval mode
# --------------------------------------------------------------------------- #

def evaluate_retrieval(questions: list[dict], base_url: str, k: int) -> None:
    client = httpx.Client(base_url=base_url, timeout=30)
    total = chapter_hits = chunk_hits = 0

    for q in questions:
        query = q["query"]
        expected_chapters = set(q.get("expected_chapters") or [])
        expected_chunks = set(q.get("expected_chunk_ids") or [])
        book_id = _resolve_book_id(client, q.get("book_key"))

        resp = client.post("/search", json={"query": query, "book_id": book_id, "k": k})
        if resp.status_code != 200:
            print(f"  SKIP  {query!r} — HTTP {resp.status_code}")
            continue

        results = resp.json()
        returned_chapters = {r["chapter_number"] for r in results}
        returned_chunks = {r["id"] for r in results}

        ch_hit = bool(expected_chapters & returned_chapters) if expected_chapters else True
        ck_hit = bool(expected_chunks & returned_chunks) if expected_chunks else True

        total += 1
        chapter_hits += ch_hit
        chunk_hits += ck_hit
        print(f"  {'HIT' if ch_hit else 'MISS':4s}  ch={sorted(returned_chapters)}  q={query!r}")

    print(f"\n{'='*60}")
    print(f"Chapter hit-rate @{k}: {chapter_hits}/{total} = {chapter_hits/max(total,1):.1%}")
    if any(q.get("expected_chunk_ids") for q in questions):
        print(f"Chunk   hit-rate @{k}: {chunk_hits}/{total} = {chunk_hits/max(total,1):.1%}")


# --------------------------------------------------------------------------- #
# Faithfulness mode
# --------------------------------------------------------------------------- #

def evaluate_faithfulness(questions: list[dict], base_url: str) -> None:
    client = httpx.Client(base_url=base_url, timeout=120)

    total = 0
    with_citation = 0
    all_valid = 0          # every citation resolves to a real chunk
    chapter_grounded = 0   # >=1 citation lands in an expected chapter

    for q in questions:
        query = q["query"]
        expected_book = q.get("book_key")
        expected_chapters = set(q.get("expected_chapters") or [])
        book_id = _resolve_book_id(client, expected_book)

        session_id: str | None = None
        try:
            answer, session_id = _stream_answer(client, query, book_id)
        except (httpx.HTTPError, RuntimeError) as exc:
            print(f"  SKIP  {query!r} — {exc}")
            continue
        finally:
            # Clean up the throwaway session created by this eval turn.
            if session_id:
                client.delete(f"/sessions/{session_id}")

        total += 1
        cites = _CITE_RE.findall(answer)  # list of (book_key, chapter, chunk_id)

        if not cites:
            print(f"  NOCITE  q={query!r}")
            continue
        with_citation += 1

        # Resolve each cited chunk and check it exists + matches the marker.
        valid = True
        cited_chapters: set[int] = set()
        for book_key, chapter_str, chunk_str in cites:
            chunk_id = int(chunk_str)
            r = client.get(f"/chunks/{chunk_id}")
            if r.status_code != 200:
                valid = False
                continue
            chunk = r.json()
            cited_chapters.add(chunk["chapter_number"])
            # Marker chapter should match the chunk's real chapter, and the book
            # should match the chunk's real book.
            if int(chapter_str) != chunk["chapter_number"]:
                valid = False
            if book_key != chunk.get("book_key"):
                valid = False

        all_valid += valid
        grounded = bool(expected_chapters & cited_chapters) if expected_chapters else True
        chapter_grounded += grounded

        flags = []
        if not valid:
            flags.append("INVALID-CITE")
        if not grounded:
            flags.append("OFF-CHAPTER")
        status = "OK" if not flags else ",".join(flags)
        print(f"  {status:16s}  cited_ch={sorted(cited_chapters)}  q={query!r}")

    print(f"\n{'='*60}")
    print(f"Answers with >=1 citation: {with_citation}/{total} = {with_citation/max(total,1):.1%}")
    print(f"All citations valid:       {all_valid}/{total} = {all_valid/max(total,1):.1%}")
    print(f"Chapter-grounded:          {chapter_grounded}/{total} = {chapter_grounded/max(total,1):.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Book Assistant evaluation")
    parser.add_argument("--mode", choices=["retrieval", "faithfulness"], default="retrieval")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--questions", default="eval/questions.yaml")
    args = parser.parse_args()

    questions = load_questions(Path(args.questions))
    print(f"Mode: {args.mode} · {len(questions)} questions\n")

    if args.mode == "retrieval":
        evaluate_retrieval(questions, args.base_url, args.k)
    else:
        evaluate_faithfulness(questions, args.base_url)


if __name__ == "__main__":
    main()
