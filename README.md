# Book Assistant

An AI-powered literary analysis tool for exploring and comparing classic novels.
Currently loaded with **Little Women** and **Pride & Prejudice** from Project Gutenberg.

## Architecture

```
Source HTML → Ingestion (BS4 + tiktoken + embeddings + LLM summaries)
           → SQLite (FTS5 + sqlite-vec)
           → Hybrid Retrieval (BM25 + cosine KNN, RRF fusion)
           → Tool-calling Agent (5-round cap, citation contract, multi-turn)
           → FastAPI (SSE token streaming) + React frontend
```

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your Azure OpenAI credentials

# 2. Build the database (one-time, ~5 min)
# Run from the repo root — data/raw and data/books.db are resolved relative to
# the current directory, and must match the paths Docker mounts.
pip install -r backend/requirements.txt
PYTHONPATH=backend python -m app.ingest
# (or simply: ./scripts/build_db.sh)

# 3. Run with Docker
docker compose up --build
```

The app is available at `http://localhost:5173` (frontend) and `http://localhost:8000` (API).

## Project Structure

```
book-assistant/
├── backend/           # FastAPI application
│   └── app/
│       ├── api/       # Route handlers
│       ├── ingest/    # Offline HTML→DB pipeline
│       ├── store/     # SQLite + FTS5 + sqlite-vec
│       ├── agent/     # Tool-calling LLM loop
│       └── llm/       # Azure OpenAI clients
├── frontend/          # React + Vite
├── data/
│   ├── raw/           # Source HTML files (committed)
│   ├── books.db       # Built book cache (not committed, rebuildable)
│   └── sessions.db    # Durable chat history (not committed, runtime data)
├── eval/              # Retrieval evaluation harness
└── scripts/           # Utility scripts
```

## Key Design Decisions

- **Two SQLite files, two lifecycles** — `books.db` is a rebuildable cache
  (drop-and-rebuild on ingest); `sessions.db` holds durable chat history, so
  re-ingesting books never wipes a user's conversations
- **Persistent chat sessions** — every conversation is saved and resumable via a
  sidebar (create / switch / rename / delete), auto-titled from its first
  message; the server loads history from the DB, so it's the source of truth
  rather than the client replaying transcripts
- **Runtime book management** — books can be uploaded (HTML) and removed from the
  UI ("Manage books"); uploads reuse the same ingest pipeline as the CLI (parse
  → chunk → embed → summarize) and non-Gutenberg HTML falls back to a single
  whole-document chapter. Derived book keys are alphanumeric slugs so citations
  keep working
- **Background ingestion** — uploads run as a tracked background job that reports
  progress (parsing → embedding → summarizing) polled by the UI; the CPU/DB-bound
  stages run in a worker thread (`asyncio.to_thread`) so a large upload never
  blocks chat streaming or search
- **Hybrid retrieval** — FTS5 BM25 + sqlite-vec cosine KNN, fused with RRF (k=60)
- **Chunking** — ~600 tokens, ~15% overlap, paragraph-aligned, chapter-bounded
- **Robust chapter parsing** — the two Gutenberg editions differ (roman-numeral
  headings vs. `CHAPTER N` markers, some prefixed with illustration captions or
  run together as `CHAPTERXXVII`); the parser detects the marker anywhere in the
  heading so both books read as fully contiguous chapters (LW 1–47, P&P 1–61)
- **Validated citations** — the agent emits `[book:chapter:chunk]` markers, but
  the loop tracks the chunk ids actually returned by tools and strips any marker
  the model invents, so the UI never renders a chip for an unseen passage
- **De-duplicated cross-book pairs** — similarity search greedily skips
  overlapping slices of the same passage on either side
- **Temperature omitted by default** — `gpt-5.1-chat` only accepts the model
  default (1) and 400s on any other value, so no `temperature` is sent unless
  `CHAT_TEMPERATURE` is set for a deployment that supports it
- **No CORS middleware** — the browser only talks to the frontend origin and
  both the dev server and nginx build proxy `/api` to the backend, so requests
  are same-origin; CORS would be dead code
- **Idempotent ingestion** — drop-and-rebuild; the DB is a cache, not a source of truth

## Evaluation

```bash
# Retrieval hit-rate @k (needs only the embedding model)
python eval/run.py

# End-to-end citation faithfulness: asks the agent, then checks every cited
# chunk exists, matches its marker's book/chapter, and lands in an expected
# chapter (needs the chat model + a built DB)
python eval/run.py --mode faithfulness
```

Golden-question `expected_chapters` were verified against the parsed chapter
texts (e.g. Beth's death → LW ch.40, Darcy's first proposal → P&P ch.34).
