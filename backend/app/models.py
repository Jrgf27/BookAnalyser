"""Pydantic v2 models shared across API, agent, and store layers."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ---- Domain objects ----


class BookMeta(BaseModel):
    id: int
    title: str
    author: str
    key: str
    word_count: int
    chapter_count: int
    summary: str | None = None


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


class ChatMessage(BaseModel):
    """A single prior turn, sent by the client to give the agent memory."""

    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    book_id: int | None = None  # None = all books
    # Existing session to append to; None starts a fresh one (created server-side).
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        return v


# ---- Sessions ----


class StoredMessage(BaseModel):
    role: str
    content: str
    created_at: float | None = None


class SessionMeta(BaseModel):
    id: str
    title: str
    book_id: int | None = None
    created_at: float
    updated_at: float
    message_count: int = 0


class SessionDetail(BaseModel):
    id: str
    title: str
    book_id: int | None = None
    created_at: float
    updated_at: float
    messages: list[StoredMessage] = Field(default_factory=list)


class IngestJobStatus(BaseModel):
    """Progress of a background book-ingestion job."""

    id: str
    title: str
    status: str            # queued | running | done | error
    stage: str = ""
    progress: float = 0.0  # 0.0 .. 1.0
    detail: str = ""
    book_id: int | None = None
    error: str | None = None


class CreateSessionRequest(BaseModel):
    title: str | None = None
    book_id: int | None = None


class RenameSessionRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        return v


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
