#!/usr/bin/env bash
# Build data/books.db from source HTML.
# Idempotent — safe to re-run.
#
# Usage:
#   ./scripts/build_db.sh          # from repo root
#   docker compose run --rm backend python -m app.ingest  # in container

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Run from the repo root so the relative RAW_DATA_DIR (data/raw) and
# DATABASE_PATH (data/books.db) resolve to the locations Docker also mounts.
# The `app` package lives under backend/, so expose it via PYTHONPATH instead
# of cd-ing into backend/ (which would break those data paths).
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f "$REPO_ROOT/.env" ]; then
    echo "ERROR: .env not found. Copy .env.example → .env and fill in credentials."
    exit 1
fi

# Export .env so pydantic-settings picks it up
set -a
source "$REPO_ROOT/.env"
set +a

echo "==> Installing dependencies..."
pip install -q -r backend/requirements.txt

echo "==> Running ingestion..."
python -m app.ingest

echo "==> Done. Database at $REPO_ROOT/data/books.db"
ls -lh "$REPO_ROOT/data/books.db"
