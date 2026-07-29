# Book Assistant

An AI-powered literary analysis tool for exploring and comparing classic novels.
Currently loaded with **Little Women** and **Pride & Prejudice** from Project Gutenberg.

## Architecture

```
Source HTML → Ingestion (BS4 + tiktoken + embeddings + LLM summaries)
           → SQLite (FTS5 + sqlite-vec)
           → Hybrid Retrieval (BM25 + cosine KNN, RRF fusion)
           → Tool-calling Agent (5-round cap, citation contract)
           → FastAPI + React frontend
```

## Quick Start

```bash
# 1. Clone and configure
cp env.example .env
# Edit .env with your Azure OpenAI credentials

# 2. Build the database (one-time, ~5 min)
cd backend
pip install -r requirements.txt
python -m app.ingest

# 3. Run with Docker
cd ..
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
│   └── books.db       # Built artifact (not committed)
├── eval/              # Retrieval evaluation harness
└── scripts/           # Utility scripts
```

## Key Design Decisions

- **Single SQLite file** — no database server, ships as one artifact
- **Hybrid retrieval** — FTS5 BM25 + sqlite-vec cosine KNN, fused with RRF (k=60)
- **Chunking** — ~600 tokens, ~15% overlap, paragraph-aligned, chapter-bounded
- **Citation contract** — agent emits `[book:chapter:chunk]` markers; frontend renders as chips
- **Idempotent ingestion** — drop-and-rebuild; the DB is a cache, not a source of truth

## Evaluation

```bash
cd eval
python run.py          # retrieval hit-rate @k, no LLM needed
```
