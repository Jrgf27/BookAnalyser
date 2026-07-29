/** Mirrors backend Pydantic models. */

export interface BookMeta {
  id: number;
  title: string;
  author: string;
  key: string;
  word_count: number;
  chapter_count: number;
}

export interface Chapter {
  id: number;
  book_id: number;
  number: number;
  title: string;
  summary: string | null;
  word_count: number;
}

export interface Chunk {
  id: number;
  book_id: number;
  chapter_id: number;
  chapter_number: number;
  text: string;
  char_start: number;
  char_end: number;
  token_count: number;
}

export interface Citation {
  book_key: string;
  chapter_number: number;
  chunk_id: number;
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  result_preview: string;
}

export interface ChatRequest {
  message: string;
  book_id?: number | null;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  trace: ToolCall[];
}

export interface PassagePair {
  chunk_a: Chunk;
  chunk_b: Chunk;
  similarity: number;
}
