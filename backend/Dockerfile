# ---- builder: compile/collect dependencies (build tools stay out of the final image) ----
FROM python:3.12-slim AS builder

# build-essential is only needed to build any source-only wheels; it never ships
# in the runtime image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime: slim image, no compilers, runs as a non-root user ----
FROM python:3.12-slim

WORKDIR /app

# Bring in the installed packages + console scripts from the builder.
COPY --from=builder /install /usr/local

# Pre-download tiktoken's BPE vocab at build time so the container doesn't fetch
# it over the network on first import (which otherwise stalls startup ~30-40s).
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken_cache
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

COPY app/ app/

# Drop root. uid/gid 1000 matches the typical host user, so the bind-mounted
# ./data stays writable and files created there aren't root-owned. If your host
# user isn't uid 1000, override with `user: "<uid>:<gid>"` on the backend service
# in docker-compose.yml (or chown ./data accordingly).
USER 1000:1000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
