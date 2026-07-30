FROM python:3.12-slim

# sqlite-vec needs to dlopen extensions; python:3.12-slim's sqlite supports this
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download tiktoken's BPE vocab at build time so the container doesn't fetch
# it over the network on first import (which otherwise stalls startup ~30-40s).
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken_cache
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
