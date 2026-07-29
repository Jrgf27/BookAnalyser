"""Pydantic v2 models shared across API, agent, and store layers."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---- Domain objects ----


class BookMeta(BaseModel):
    id: int
    title: str
    author: str
    key: str
    word_count: int
    chapter_count: int


class Chapter(BaseModel):
    id: int
    book_id: int
    number: int
    title: str
    summary: str | None = None
    word_count: int


class Chunk(BaseModel):
    id: int
    book_id: int
    chapter_id: int
    chapter_number: int
    text: str
    char_start: int
    char_end: int
    token_count: int


# ---- API request / response ----


class Citation(BaseModel):
    """Parsed from inline markers like [pp:12:7]."""

    book_key: str
    chapter_number: int
    chunk_id: int


class ToolCall(BaseModel):
    tool: str
    args: dict
    result_preview: str = ""


class ChatRequest(BaseModel):
    message: str
    book_id: int | None = None  # None = all books


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    trace: list[ToolCall] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    book_id: int | None = None
    k: int = 10


class CompareRequest(BaseModel):
    book_id_a: int
    book_id_b: int
    top_k: int = 20


class PassagePair(BaseModel):
    chunk_a: Chunk
    chunk_b: Chunk
    similarity: float
