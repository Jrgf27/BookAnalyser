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

cd "$REPO_ROOT/backend"

if [ ! -f "$REPO_ROOT/.env" ]; then
    echo "ERROR: .env not found. Copy env.example → .env and fill in credentials."
    exit 1
fi

# Export .env so pydantic-settings picks it up
set -a
source "$REPO_ROOT/.env"
set +a

echo "==> Installing dependencies..."
pip install -q -r requirements.txt

echo "==> Running ingestion..."
python -m app.ingest

echo "==> Done. Database at $REPO_ROOT/data/books.db"
ls -lh "$REPO_ROOT/data/books.db"
