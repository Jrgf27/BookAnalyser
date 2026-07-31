"""Tests for the Azure client's retry wrapper — the resilience logic that keeps
transient rate-limit/timeout/5xx failures from surfacing to the user.

The real network calls are never made: we drive ``_with_retry`` with fake async
functions that raise controlled errors. ``base_delay=0`` keeps backoff instant.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from openai import APIConnectionError

from app.llm.azure import _with_retry


def _conn_error() -> APIConnectionError:
    return APIConnectionError(message="boom", request=httpx.Request("GET", "http://x"))


class TestWithRetry:
    def test_returns_immediately_on_success(self) -> None:
        async def fn() -> str:
            return "ok"

        assert asyncio.run(_with_retry(fn, what="t")) == "ok"

    def test_retries_transient_then_succeeds(self) -> None:
        calls = {"n": 0}

        async def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise _conn_error()
            return "recovered"

        result = asyncio.run(_with_retry(fn, what="t", base_delay=0))
        assert result == "recovered"
        assert calls["n"] == 3  # failed twice, succeeded on the third

    def test_non_retryable_propagates_without_retry(self) -> None:
        calls = {"n": 0}

        async def fn() -> None:
            calls["n"] += 1
            raise ValueError("client error — not retryable")

        with pytest.raises(ValueError):
            asyncio.run(_with_retry(fn, what="t", base_delay=0))
        assert calls["n"] == 1  # tried exactly once, then gave up

    def test_exhausts_attempts_then_raises(self) -> None:
        calls = {"n": 0}

        async def fn() -> None:
            calls["n"] += 1
            raise _conn_error()

        with pytest.raises(APIConnectionError):
            asyncio.run(_with_retry(fn, what="t", max_attempts=3, base_delay=0))
        assert calls["n"] == 3  # capped at max_attempts
