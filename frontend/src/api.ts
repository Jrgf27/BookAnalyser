/** Thin API client — all requests go through the /api proxy. */

import type { BookMeta, Chapter, ChatRequest, ChatResponse, Chunk } from './types';

const BASE = '/api';

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export function fetchBooks(): Promise<BookMeta[]> {
  return json('/books');
}

export function fetchOutline(bookId: number): Promise<Chapter[]> {
  return json(`/books/${bookId}/outline`);
}

export function fetchChunk(chunkId: number, window = 2): Promise<Chunk & { context?: string }> {
  return json(`/chunks/${chunkId}?window=${window}`);
}

export function sendChat(req: ChatRequest): Promise<ChatResponse> {
  return json('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export function searchChunks(query: string, bookId?: number | null, k = 10): Promise<Chunk[]> {
  return json('/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, book_id: bookId ?? null, k }),
  });
}
