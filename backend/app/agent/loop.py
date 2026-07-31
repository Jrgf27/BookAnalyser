"""Tool-calling agent loop with round cap and context budget."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from app.config import Settings
from app.models import ChatMessage, ChatResponse, Citation, ToolCall
from app.store.sqlite_store import SqliteChunkStore
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOL_SCHEMAS, dispatch_tool
from app.llm.azure import chat_completion_stream

logger = logging.getLogger(__name__)

# Regex for citation markers: [book_key:chapter:chunk_id].  Keys are lowercase
# alphanumeric slugs (e.g. `lw`, `pp`, `greatexpectations2`) so uploaded books
# with derived keys cite correctly.
_CITE_RE = re.compile(r"\[([a-z0-9]+):(\d+):(\d+)\]")


def _parse_citations(text: str) -> list[Citation]:
    return [
        Citation(book_key=m.group(1), chapter_number=int(m.group(2)), chunk_id=int(m.group(3)))
        for m in _CITE_RE.finditer(text)
    ]


def _drop_unsupported_citations(
    text: str, surfaced_ids: set[int]
) -> tuple[str, list[Citation]]:
    """Remove citation markers whose chunk id was never returned by a tool.

    The system prompt forbids fabricated citations, but nothing stops the model
    from emitting one anyway.  We strip such markers from the answer so the UI
    never renders a chip that points at a passage the model never actually saw,
    and return only the validated citations.
    """
    dropped = 0

    def _keep(m: "re.Match[str]") -> str:
        nonlocal dropped
        if int(m.group(3)) in surfaced_ids:
            return m.group(0)
        dropped += 1
        return ""

    cleaned = _CITE_RE.sub(_keep, text)
    if dropped:
        logger.warning("Dropped %d unsupported citation marker(s)", dropped)
    # Tidy any double spaces left where a marker was removed
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned, _parse_citations(cleaned)


# Roles we accept from client-supplied history; anything else is ignored so a
# malformed transcript can't inject tool/system messages into the context.
_ALLOWED_HISTORY_ROLES = {"user", "assistant"}


def _build_initial_messages(
    user_message: str,
    store: SqliteChunkStore,
    *,
    book_id: int | None,
    history: list[ChatMessage] | None,
) -> list[dict[str, Any]]:
    """Assemble the system prompt, optional scope note, prior turns, and the
    current user message into the message list the loop starts from."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # If the user scoped the conversation to one book, tell the model (so it
    # cites the right book and won't reach for the other) and enforce it in the
    # tool layer via scope_book_id below.
    if book_id is not None:
        row = store.conn.execute(
            "SELECT title, key FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        if row is not None:
            messages.append({
                "role": "system",
                "content": (
                    f"The user has restricted this conversation to '{row['title']}' "
                    f"(book_id={book_id}, book_key={row['key']}). Only use information "
                    f"from this book, and pass book_id={book_id} to search/get_outline. "
                    f"Do not reference the other book."
                ),
            })

    # Replay prior turns so follow-ups ("what about her sister?") have context.
    for turn in history or []:
        if turn.role in _ALLOWED_HISTORY_ROLES and turn.content.strip():
            messages.append({"role": turn.role, "content": turn.content})

    messages.append({"role": "user", "content": user_message})
    return messages


# ---- Streaming agent ----


def _done_event(
    raw_answer: str, surfaced_ids: set[int], trace: list[ToolCall]
) -> dict[str, Any]:
    """Build the terminal ``done`` event, validating citations and shaping the
    payload through ``ChatResponse`` so it matches the documented contract."""
    answer, citations = _drop_unsupported_citations(raw_answer, surfaced_ids)
    payload = ChatResponse(answer=answer, citations=citations, trace=trace)
    return {"type": "done", **payload.model_dump()}


def _accumulate_tool_calls(
    delta_tool_calls: Any, acc: dict[int, dict[str, str]]
) -> None:
    """Merge streamed tool-call deltas (which arrive fragmented by index)."""
    for tc in delta_tool_calls:
        slot = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
        if tc.id:
            slot["id"] = tc.id
        if tc.function and tc.function.name:
            slot["name"] += tc.function.name
        if tc.function and tc.function.arguments:
            slot["args"] += tc.function.arguments


async def run_agent_stream(
    user_message: str,
    store: SqliteChunkStore,
    settings: Settings,
    *,
    book_id: int | None = None,
    history: list[ChatMessage] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Tool-calling loop that yields events as they happen.

    Event shapes (all dicts):
      {"type": "tool",  "tool", "args", "result_preview"}   — a tool ran
      {"type": "token", "text"}                             — answer delta
      {"type": "done",  "answer", "citations", "trace"}     — final, validated

    Tokens stream raw (may include citation markers); the terminal ``done``
    event carries the citation-validated answer so the client can swap in the
    cleaned text and render only supported chips.
    """
    max_rounds = settings.agent_max_rounds
    budget = settings.context_budget_chars

    messages = _build_initial_messages(
        user_message, store, book_id=book_id, history=history
    )

    trace: list[ToolCall] = []
    surfaced_ids: set[int] = set()

    for round_num in range(max_rounds):
        logger.info("Agent (stream) round %d/%d", round_num + 1, max_rounds)

        stream = await chat_completion_stream(
            messages=messages, tools=TOOL_SCHEMAS, settings=settings
        )

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}

        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "token", "text": delta.content}
            if delta.tool_calls:
                _accumulate_tool_calls(delta.tool_calls, tool_acc)

        # No tool calls this round → the streamed content is the final answer.
        if not tool_acc:
            yield _done_event("".join(content_parts), surfaced_ids, trace)
            return

        # Reconstruct the assistant turn (with tool calls) for the transcript.
        ordered = [tool_acc[i] for i in sorted(tool_acc)]
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["args"]},
                }
                for slot in ordered
            ],
        })

        for slot in ordered:
            fn_name = slot["name"]
            try:
                fn_args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                fn_args = {}
            logger.info("Tool call: %s(%s)", fn_name, fn_args)

            result_str, ids = await dispatch_tool(
                fn_name, fn_args, store, settings, budget=budget, scope_book_id=book_id
            )
            surfaced_ids |= ids

            tc = ToolCall(tool=fn_name, args=fn_args, result_preview=result_str[:500])
            trace.append(tc)
            yield {"type": "tool", **tc.model_dump()}

            messages.append({
                "role": "tool",
                "tool_call_id": slot["id"],
                "content": result_str,
            })

    # Exhausted rounds — stream a final answer without tools.
    messages.append({
        "role": "user",
        "content": "Please provide your final answer now based on the information gathered.",
    })
    stream = await chat_completion_stream(messages=messages, tools=None, settings=settings)
    content_parts = []
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            content_parts.append(delta.content)
            yield {"type": "token", "text": delta.content}

    yield _done_event("".join(content_parts), surfaced_ids, trace)
