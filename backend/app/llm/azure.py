"""Azure OpenAI client — chat completions + embeddings with batching and retry."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncAzureOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.config import Settings

logger = logging.getLogger(__name__)

_chat_client: AsyncAzureOpenAI | None = None
_embed_client: AsyncAzureOpenAI | None = None

T = TypeVar("T")

# Transient errors worth retrying: rate limits, timeouts, dropped connections,
# and 5xx responses.  Client errors (4xx other than 429) are not retried — they
# won't succeed on a retry and would just waste the budget.
_RETRYABLE = (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)


async def _with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
) -> T:
    """Call ``fn`` with exponential backoff + jitter on transient errors.

    Honours ``Retry-After`` when the provider supplies it on a 429, otherwise
    backs off exponentially (base_delay * 2**attempt) with full jitter, capped
    at ``max_delay``.  Non-retryable errors propagate immediately.
    """
    for attempt in range(max_attempts):
        try:
            return await fn()
        except _RETRYABLE as exc:
            if attempt == max_attempts - 1:
                logger.error("%s failed after %d attempts: %s", what, max_attempts, exc)
                raise
            retry_after = getattr(
                getattr(exc, "response", None), "headers", {}
            ).get("retry-after") if hasattr(exc, "response") else None
            if retry_after:
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = base_delay * (2 ** attempt)
            else:
                delay = min(max_delay, base_delay * (2 ** attempt))
                delay = random.uniform(0, delay)  # full jitter
            logger.warning(
                "%s attempt %d/%d hit %s; retrying in %.1fs",
                what, attempt + 1, max_attempts, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
    # Unreachable — the loop either returns or raises on the last attempt.
    raise RuntimeError(f"{what}: retry loop exited unexpectedly")


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
    temperature: float | None = None,
) -> Any:
    """Send a chat completion request, with optional tool definitions.

    ``temperature`` falls back to ``settings.chat_temperature``.  When the
    resolved value is ``None`` the parameter is omitted so the model uses its
    default — required for deployments like gpt-5.1-chat that reject any
    non-default temperature.
    """
    client = _get_chat_client(settings)
    kwargs: dict[str, Any] = {
        "model": settings.azure_chat_deployment,
        "messages": messages,
    }
    temp = settings.chat_temperature if temperature is None else temperature
    if temp is not None:
        kwargs["temperature"] = temp
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return await _with_retry(
        lambda: client.chat.completions.create(**kwargs), what="chat_completion"
    )


async def chat_completion_stream(
    messages: list[dict[str, Any]],
    *,
    settings: Settings,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> Any:
    """Like ``chat_completion`` but returns a streaming response.

    Retry wraps only the initial request that establishes the stream — once
    tokens start flowing we can't safely restart mid-stream, so transient
    failures during iteration surface to the caller.
    """
    client = _get_chat_client(settings)
    kwargs: dict[str, Any] = {
        "model": settings.azure_chat_deployment,
        "messages": messages,
        "stream": True,
    }
    temp = settings.chat_temperature if temperature is None else temperature
    if temp is not None:
        kwargs["temperature"] = temp
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return await _with_retry(
        lambda: client.chat.completions.create(**kwargs),
        what="chat_completion_stream",
    )


# ---- Embeddings ----


async def get_embedding(text: str, settings: Settings) -> list[float]:
    """Embed a single text string."""
    client = _get_embed_client(settings)
    resp = await _with_retry(
        lambda: client.embeddings.create(
            model=settings.azure_embedding_deployment,
            input=[text],
            dimensions=settings.embedding_dimensions,
        ),
        what="get_embedding",
    )
    return resp.data[0].embedding


async def get_embeddings_batched(
    texts: list[str],
    settings: Settings,
    *,
    batch_size: int | None = None,
    on_batch: "Callable[[int, int], None] | None" = None,
) -> list[list[float]]:
    """Embed a list of texts in batches, respecting API limits.

    ``on_batch(done, total)`` is invoked after each batch with the running count
    of embedded texts, for progress reporting.
    """
    if batch_size is None:
        batch_size = settings.embedding_batch_size
    client = _get_embed_client(settings)

    total = len(texts)
    all_embeddings: list[list[float]] = []

    for i in range(0, total, batch_size):
        batch = texts[i : i + batch_size]
        logger.info("Embedding batch %d–%d of %d", i, i + len(batch), total)
        resp = await _with_retry(
            lambda batch=batch: client.embeddings.create(
                model=settings.azure_embedding_deployment,
                input=batch,
                dimensions=settings.embedding_dimensions,
            ),
            what="get_embeddings_batched",
        )
        # Ensure order matches input
        sorted_data = sorted(resp.data, key=lambda d: d.index)
        all_embeddings.extend(d.embedding for d in sorted_data)

        if on_batch is not None:
            on_batch(min(i + len(batch), total), total)

        # Small delay between batches to be polite to rate limits
        if i + batch_size < total:
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
        # No explicit temperature — see chat_completion; the default deployment
        # rejects non-default values.
    )
    return resp.choices[0].message.content or ""
