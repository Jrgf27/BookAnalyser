/** Mirrors backend Pydantic models. */

export interface BookMeta {
  id: number;
  title: string;
  author: string;
  key: string;
  word_count: number;
  chapter_count: number;
  summary: string | null;
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
  session_id?: string | null;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  trace: ToolCall[];
}

/** Events emitted by the SSE /chat/stream endpoint. */
export type StreamEvent =
  | { type: 'session'; session_id: string; created: boolean }
  | { type: 'token'; text: string }
  | { type: 'tool'; tool: string; args: Record<string, unknown>; result_preview: string }
  | { type: 'done'; answer: string; citations: Citation[]; trace: ToolCall[] }
  | { type: 'error'; message: string };

// ---- Sessions ----

export interface SessionMeta {
  id: string;
  title: string;
  book_id: number | null;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface StoredMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at?: number | null;
}

export interface SessionDetail {
  id: string;
  title: string;
  book_id: number | null;
  created_at: number;
  updated_at: number;
  messages: StoredMessage[];
}

export interface IngestJobStatus {
  id: string;
  title: string;
  status: 'queued' | 'running' | 'done' | 'error';
  stage: string;
  progress: number; // 0..1
  detail: string;
  book_id: number | null;
  error: string | null;
}

export interface PassagePair {
  chunk_a: Chunk;
  chunk_b: Chunk;
  similarity: number;
}
