"""Azure OpenAI client — chat completions + embeddings with batching and retry."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncAzureOpenAI

from app.config import Settings

logger = logging.getLogger(__name__)

_chat_client: AsyncAzureOpenAI | None = None
_embed_client: AsyncAzureOpenAI | None = None


def _get_chat_client(settings: Settings) -> AsyncAzureOpenAI:
    """Client for chat completions."""
    global _chat_client
    if _chat_client is None:
        _chat_client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
    return _chat_client


def _get_embed_client(settings: Settings) -> AsyncAzureOpenAI:
    """Client for embeddings (may use a different api_version)."""
    global _embed_client
    if _embed_client is None:
        _embed_client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_embedding_api_version,
        )
    return _embed_client


# ---- Chat ----


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    settings: Settings,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.3,
) -> Any:
    """Send a chat completion request, with optional tool definitions."""
    client = _get_chat_client(settings)
    kwargs: dict[str, Any] = {
        "model": settings.azure_chat_deployment,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return await client.chat.completions.create(**kwargs)


# ---- Embeddings ----


async def get_embedding(text: str, settings: Settings) -> list[float]:
    """Embed a single text string."""
    client = _get_embed_client(settings)
    resp = await client.embeddings.create(
        model=settings.azure_embedding_deployment,
        input=[text],
        dimensions=settings.embedding_dimensions,
    )
    return resp.data[0].embedding


async def get_embeddings_batched(
    texts: list[str],
    settings: Settings,
    *,
    batch_size: int | None = None,
) -> list[list[float]]:
    """Embed a list of texts in batches, respecting API limits."""
    if batch_size is None:
        batch_size = settings.embedding_batch_size
    client = _get_embed_client(settings)

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        logger.info("Embedding batch %d–%d of %d", i, i + len(batch), len(texts))
        resp = await client.embeddings.create(
            model=settings.azure_embedding_deployment,
            input=batch,
            dimensions=settings.embedding_dimensions,
        )
        # Ensure order matches input
        sorted_data = sorted(resp.data, key=lambda d: d.index)
        all_embeddings.extend(d.embedding for d in sorted_data)

        # Small delay between batches to be polite to rate limits
        if i + batch_size < len(texts):
            await asyncio.sleep(0.2)

    return all_embeddings


# ---- One-shot summarize ----


async def summarize_text(
    text: str,
    prompt: str,
    settings: Settings,
    *,
    max_tokens: int = 300,
) -> str:
    """One-shot summarization via chat completion."""
    resp = await chat_completion(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        settings=settings,
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""
