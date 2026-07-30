# Book Assistant

An AI-powered literary-analysis tool for a publishing editorial team. Upload
books, then chat with an assistant that answers questions, retrieves relevant
passages, and compares content across titles — always grounded in the source
text with inline, verifiable citations.

Built with FastAPI + a tool-calling LLM agent over hybrid retrieval, and a
React frontend with streaming responses and persistent chat history.

## Features

- **Grounded chat** — ask about themes, characters, plot, or writing style; the
  agent searches the books and cites every claim with clickable `[book:chapter:chunk]`
  markers that open the exact source passage.
- **Cross-book comparison** — find and compare thematically similar passages
  between two books, with evidence from each.
- **Persistent sessions** — every conversation is saved and resumable from a
  sidebar (create / switch / rename / delete), auto-titled from its first message.
- **Streaming answers** — tokens stream live over SSE, and a plain-language
  "Sources consulted" panel shows what the assistant did to ground its answer.
- **Book management in the UI** — upload books (HTML) or remove them at runtime;
  ingestion runs as a background job with a live progress bar.
- **Chapter outlines** — scope to a single book to browse its per-chapter
  summaries.
- **Portable data** — download your book library and chat history as SQLite
  files, and restore them later (or on another machine).

## Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env with your Azure OpenAI credentials

# 2. Run
docker compose up --build
```

Open **http://localhost:5173** (the API is on `http://localhost:8000`).

The library starts **empty**. Add books from the UI: click **Manage books**,
enter a title/author, and upload an HTML file. Ingestion (parse → chunk → embed
→ summarize) runs in the background with a progress bar, and the book appears in
the library once it's ready. Two sample books are included under `data/raw/`
(Little Women and Pride & Prejudice, from Project Gutenberg) — upload them to get
started.

### Configuration

All settings are read from `.env` (see `.env.example`). The essentials:

| Variable | Purpose |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` | Azure OpenAI access |
| `AZURE_CHAT_DEPLOYMENT` | Chat/agent model deployment |
| `AZURE_EMBEDDING_DEPLOYMENT` | Embedding model deployment |
| `EMBEDDING_DIMENSIONS` | Embedding size (default 3072) |

Chat and uploads require valid Azure credentials; the book list, outlines, and
sessions render without them.

## How It Works

```
Upload HTML → Ingestion (BS4 parse + tiktoken chunk + embeddings + LLM summaries)
           → SQLite (FTS5 full-text + sqlite-vec vectors)
           → Hybrid retrieval (BM25 + cosine KNN, fused with RRF)
           → Tool-calling agent (5-round cap, citation contract, multi-turn)
           → FastAPI (SSE token streaming) + React frontend
```

The agent is given five tools and decides which to call: `list_books`,
`get_outline`, `search` (hybrid), `get_context` (surrounding passage), and
`find_similar_passages` (cross-book). Retrieval fuses keyword (FTS5 BM25) and
semantic (sqlite-vec cosine KNN) results via Reciprocal Rank Fusion.

## Project Structure

```
book-assistant/
├── backend/                 # FastAPI application
│   └── app/
│       ├── api/             # Routes: books, chunks, search, chat, sessions
│       ├── ingest/          # HTML→DB pipeline + background jobs (upload-driven)
│       ├── store/           # SQLite: chunk store (FTS5 + sqlite-vec) + sessions
│       ├── agent/           # Tool-calling loop, tools, prompts
│       └── llm/             # Azure OpenAI clients (retry, streaming, embeddings)
├── frontend/                # React + Vite (chat, sidebar, book manager, outline)
├── data/
│   ├── raw/                 # Sample HTML books to upload via the UI
│   ├── books.db             # Book cache (not committed, built from uploads)
│   └── sessions.db          # Durable chat history (not committed, runtime data)
├── eval/                    # Retrieval + faithfulness evaluation harness
└── scripts/                 # Utility scripts (Azure connectivity probe)
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/books` | List ready books |
| `POST` | `/books` | Upload an HTML book → returns an ingestion job |
| `GET` | `/books/jobs/{id}` | Poll ingestion progress |
| `DELETE` | `/books/{id}` | Remove a book |
| `GET` | `/books/{id}/outline` | Chapter titles + summaries |
| `POST` | `/search` | Hybrid passage search |
| `POST` | `/compare` | Cross-book similar-passage pairs |
| `GET` | `/chunks/{id}` | A chunk + surrounding context |
| `POST` | `/chat/stream` | Session-backed chat over SSE |
| `GET`/`POST`/`GET`/`PATCH`/`DELETE` | `/sessions[...]` | Session CRUD |
| `GET` | `/export/{books,sessions}.db` | Download a DB snapshot |
| `POST` | `/import/{books,sessions}.db` | Restore a DB from an upload |
| `GET` | `/health` | Liveness check |

## Key Design Decisions

- **Two SQLite files, two lifecycles** — `books.db` is a rebuildable cache;
  `sessions.db` holds durable chat history, so removing/re-adding books never
  wipes a user's conversations.
- **Sessions are server-owned** — the server loads history from the DB and is the
  source of truth, rather than trusting the client to replay transcripts.
- **Background, non-blocking ingestion** — uploads run as a tracked job that
  reports progress (parsing → embedding → summarizing); the CPU/DB-bound stages
  run off the event loop via `asyncio.to_thread`, so a large upload never blocks
  chat streaming or search.
- **Crash-safe ingestion** — a book is marked `ready` only after ingestion fully
  completes. The API/agent only show ready books, a failed ingest cleans up its
  partial data, and a startup sweep removes anything left half-built by a crash.
- **Best-effort summaries** — chapter/book summaries are non-essential, so an
  Azure content-filter rejection on one chapter is logged and skipped rather than
  failing the whole upload.
- **Unique books** — `(title, author)` must be unique; duplicate uploads are
  rejected with a clear message.
- **Hybrid retrieval** — FTS5 BM25 + sqlite-vec cosine KNN, fused with RRF (k=60).
- **Chunking** — ~600 tokens, ~15% overlap, paragraph-aligned, chapter-bounded.
- **Robust chapter parsing** — handles differing Gutenberg conventions (roman
  numerals vs. `CHAPTER N`, caption-prefixed or run-together headings), and falls
  back to a single whole-document chapter for arbitrary HTML. Source line-wrap
  newlines are collapsed so passages read as continuous prose.
- **Validated citations** — the loop tracks the chunk ids tools actually returned
  and strips any citation marker the model invents, so the UI never links to a
  passage the model didn't see. Book keys are alphanumeric slugs so uploaded-book
  citations keep working.
- **De-duplicated cross-book pairs** — similarity search greedily skips
  overlapping slices of the same passage on either side.
- **Resilient Azure client** — retry with exponential backoff + jitter on rate
  limits/timeouts/5xx; `temperature` is omitted by default because `gpt-5.1-chat`
  rejects any non-default value.
- **Portable, consistent backups** — export/import serve a snapshot made with
  SQLite's online backup API (not the raw file), so WAL-pending writes can't
  produce a torn copy; import validates the file is SQLite with the expected
  tables before a full-replace restore.
- **Client-facing errors** — the frontend shows friendly messages (e.g. "this
  passage is no longer available" when its book was removed), never raw JSON.
- **Same-origin, no CORS** — both the Vite dev server and the nginx build proxy
  `/api` to the backend, so CORS middleware would be dead code.

## Development

Run the two dev servers directly (nicer than rebuilding images):

```bash
# Backend (from the repo root, so .env and data/ resolve)
pip install -r backend/requirements.txt
PYTHONPATH=backend uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

The Vite dev server proxies `/api` to the backend, mirroring the Docker setup.

### Tests

```bash
cd backend && PYTHONPATH=. python -m pytest
```

Covers parsing, chunking, the store (incl. schema migration + crash cleanup),
citation validation, scope enforcement, sessions, book upload/delete, and
background ingestion with progress reporting.

## Evaluation

```bash
# Retrieval hit-rate @k (needs only the embedding model)
python eval/run.py

# End-to-end citation faithfulness: asks the agent, then verifies every cited
# chunk exists, matches its marker's book/chapter, and lands in an expected
# chapter (needs the chat model and the sample books uploaded via the UI)
python eval/run.py --mode faithfulness
```

Golden-question `expected_chapters` were verified against the parsed chapter
texts (e.g. Beth's death → LW ch.40, Darcy's first proposal → P&P ch.34).

## Assumptions & Trade-offs

- **Single-user, local tool** — no authentication; sessions and books are global.
  Ingestion jobs are tracked in memory, so in-flight progress is lost on restart
  (the ingested book itself is persisted).
- **One shared SQLite connection** — fine for a read-mostly local workload; a
  server deployment would want a connection pool.
- **HTML input only** — uploads accept HTML; non-Gutenberg HTML still ingests via
  the single-chapter fallback.
