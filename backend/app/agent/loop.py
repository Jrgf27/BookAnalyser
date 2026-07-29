"""Tool-calling agent loop with round cap and context budget."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import Settings
from app.models import ChatResponse, Citation, ToolCall
from app.store.sqlite_store import SqliteChunkStore
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOL_SCHEMAS, dispatch_tool
from app.llm.azure import chat_completion

logger = logging.getLogger(__name__)

# Regex for citation markers: [book_key:chapter:chunk_id]
_CITE_RE = re.compile(r"\[([a-z]+):(\d+):(\d+)\]")


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


async def run_agent(
    user_message: str,
    store: SqliteChunkStore,
    settings: Settings,
    *,
    book_id: int | None = None,
) -> ChatResponse:
    """Run the tool-calling loop for up to max_rounds."""
    max_rounds = settings.agent_max_rounds
    budget = settings.context_budget_chars

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

    messages.append({"role": "user", "content": user_message})

    trace: list[ToolCall] = []
    surfaced_ids: set[int] = set()

    for round_num in range(max_rounds):
        logger.info("Agent round %d/%d", round_num + 1, max_rounds)

        response = await chat_completion(
            messages=messages,
            tools=TOOL_SCHEMAS,
            settings=settings,
        )

        choice = response.choices[0]

        # If the model is done (no tool calls), return the answer
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            answer = choice.message.content or ""
            answer, citations = _drop_unsupported_citations(answer, surfaced_ids)
            return ChatResponse(answer=answer, citations=citations, trace=trace)

        # Process each tool call
        messages.append(choice.message.model_dump())

        for tc in choice.message.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)
            logger.info("Tool call: %s(%s)", fn_name, fn_args)

            result_str, ids = await dispatch_tool(
                fn_name, fn_args, store, settings, budget=budget, scope_book_id=book_id
            )
            surfaced_ids |= ids

            trace.append(ToolCall(
                tool=fn_name,
                args=fn_args,
                result_preview=result_str[:500],
            ))

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

    # Exhausted rounds — ask model for a final answer without tools
    messages.append({
        "role": "user",
        "content": "Please provide your final answer now based on the information gathered.",
    })
    response = await chat_completion(messages=messages, tools=None, settings=settings)
    answer = response.choices[0].message.content or ""
    answer, citations = _drop_unsupported_citations(answer, surfaced_ids)
    return ChatResponse(answer=answer, citations=citations, trace=trace)
