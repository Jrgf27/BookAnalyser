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
        {"role": "user", "content": user_message},
    ]

    trace: list[ToolCall] = []

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
            citations = _parse_citations(answer)
            return ChatResponse(answer=answer, citations=citations, trace=trace)

        # Process each tool call
        messages.append(choice.message.model_dump())

        for tc in choice.message.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments)
            logger.info("Tool call: %s(%s)", fn_name, fn_args)

            result_str = await dispatch_tool(
                fn_name, fn_args, store, settings, budget=budget
            )

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
    citations = _parse_citations(answer)
    return ChatResponse(answer=answer, citations=citations, trace=trace)
