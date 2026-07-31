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

Open **http://localhost:5173**. The backend isn't published to the host — the
frontend proxies `/api` to it over the Compose network — so there's nothing to
open on `:8000`. To reach the API directly for debugging, add a
`ports: ["8000:8000"]` mapping to the `backend` service in `docker-compose.yml`.
(In local dev without Docker it still runs on `http://localhost:8000`.)

On first boot the two bundled sample books (Little Women and Pride & Prejudice,
from Project Gutenberg, under `data/raw/`) are **ingested automatically** in the
background — you'll see them appear with a progress bar and can start chatting
once they're ready. This runs only when the library is empty, so it won't
duplicate anything or re-run on later starts. Disable it with `SEED_ON_START=false`
to begin with an empty library.

To add more books, click **Manage books**, enter a title/author, and upload an
HTML file. Ingestion (parse → chunk → embed → summarize) runs in the background
with a progress bar, and the book appears in the library once it's ready. Only
HTML uploads are accepted (see below).

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
│   ├── raw/                 # Sample HTML books (auto-seeded on first boot)
│   ├── books.db             # Book cache (not committed, built from uploads)
│   ├── sessions.db          # Durable chat history (not committed, runtime data)
│   └── ingest_queue.db      # Durable ingestion backlog (not committed, runtime)
├── eval/                    # Retrieval + faithfulness evaluation harness
└── scripts/                 # Utility scripts (Azure connectivity probe)
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/books` | List ready books |
| `POST` | `/books` | Upload an HTML book → returns an ingestion job |
| `GET` | `/books/jobs` | List active + recently-finished ingestions (incl. seed jobs) |
| `GET` | `/books/jobs/{id}` | Poll one ingestion's progress |
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

- **Separate SQLite files, separate lifecycles** — `books.db` is a rebuildable
  cache; `sessions.db` holds durable chat history (so removing/re-adding books
  never wipes conversations); `ingest_queue.db` is a durable backlog of pending
  ingestions. Each has its own connection, which also keeps the queue's writes
  off the chunk-store connection.
- **Durable ingestion queue** — an upload is written to `ingest_queue.db` (raw
  HTML payload + coarse status) *before* ingesting, and its row is deleted only
  when the book finishes (success or failure). So a book that's still queued or
  mid-ingest when the server stops is re-scheduled automatically on the next
  boot — nothing pending is lost. A row whose book already exists (the process
  died after the book committed but before the row was cleared) is detected and
  dropped, so resume never creates a duplicate. Live progress/stage detail stays
  in memory (the `JobRegistry`); only the minimal durable facts are persisted, so
  the queue takes just a few writes per book.
- **Sessions are server-owned** — the server loads history from the DB and is the
  source of truth, rather than trusting the client to replay transcripts.
- **Auto-seed on first boot** — if the library is empty at startup, the bundled
  sample books are ingested via the normal background pipeline (so they show up
  with progress bars). It's idempotent — guarded by an empty-library check, so it
  never duplicates books or re-runs on a warm database — scheduling-only so it
  never blocks startup or the healthcheck, and toggleable via `SEED_ON_START`.
  Seeding at container **startup** rather than image-build time is deliberate:
  embedding/summarization need Azure credentials and network at run time, which
  shouldn't be baked into an image layer.
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
# Backend (pytest) — requirements-dev.txt adds pytest on top of requirements.txt
cd backend && pip install -r requirements-dev.txt && PYTHONPATH=. python -m pytest

# Frontend (Vitest + Testing Library)
cd frontend && npm test
```

The backend suite covers parsing, chunking, the store (incl. schema migration +
crash cleanup), citation validation, scope enforcement, sessions, book
upload/delete, background ingestion with progress reporting, ingestion
serialization, and the durable queue (persistence, terminal cleanup, resume).
The frontend suite covers the API client and the book-manager queue UI (per-job
progress, queued state, upload-while-busy, delete gating).

### Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on pull requests,
with two parallel jobs: **backend** (`pip install` + `pytest`) and **frontend**
(`npm ci` + `npm run build` + `npm test`). Both are hermetic — the Azure-touching
code is stubbed in tests, so no secrets are required — with dependency caching
and cancellation of superseded runs.

## Evaluation

Both modes hit a running backend at `http://localhost:8000`, so start the server
first (see [Development](#development)) and pass `--base-url` if it's elsewhere.

```bash
# Retrieval hit-rate @k (needs only the embedding model)
python eval/run.py

# End-to-end citation faithfulness: asks the agent, then verifies every cited
# chunk exists, matches its marker's book/chapter, and lands in an expected
# chapter (needs the chat model and the sample books ingested — they're
# auto-seeded on first boot, or add them via the UI)
python eval/run.py --mode faithfulness
```

Golden-question `expected_chapters` were verified against the parsed chapter
texts (e.g. Beth's death → LW ch.40, Darcy's first proposal → P&P ch.34).

## Assumptions & Trade-offs

- **Single-user, local tool** — no authentication; sessions and books are global.
  Live ingestion *progress* (percentage/stage) is in memory, so a restart resets
  the progress bar — but the queued work itself is durable (`ingest_queue.db`) and
  resumes automatically, and finished books are persisted.
- **One shared SQLite connection, serialized writes** — fine for a read-mostly
  local workload; a server deployment would want a connection pool. Because the
  connection isn't safe for concurrent writes across threads, all ingestion (the
  first-boot seed and any uploads) runs behind a single process-wide lock: a book
  uploaded while the samples are still seeding simply queues (shown as "waiting")
  and ingests as soon as the current one finishes, rather than racing the
  connection.
- **HTML input only (by design)** — uploads accept HTML (`.html`/`.htm`, or
  `text/html`) and nothing else. This is a deliberate constraint: the provided
  dataset was supplied as HTML, so the ingestion pipeline and chapter parser are
  built around that format. The UI file picker is restricted to HTML and the API
  rejects any other type with a clear `400` (`"Only HTML files (.html/.htm) are
  supported."`), so non-HTML uploads fail fast and explicitly rather than
  ingesting incorrectly. Non-Gutenberg HTML still ingests via the single-chapter
  fallback.
