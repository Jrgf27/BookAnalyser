/** Thin API client — all requests go through the /api proxy. */

import type {
  BookMeta,
  Chapter,
  ChatRequest,
  Chunk,
  IngestJobStatus,
  SessionDetail,
  SessionMeta,
  StreamEvent,
} from './types';

const BASE = '/api';

/**
 * Error carrying the HTTP status and a human-readable message. Prefers
 * FastAPI's `detail` field over the raw JSON body, so UI can show something
 * sensible instead of a serialized error object.
 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, body: string) {
    let message = body;
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === 'string') message = parsed.detail;
    } catch {
      // body wasn't JSON — keep it as-is
    }
    super(message || `Request failed (${status})`);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  return res.json() as Promise<T>;
}

function jsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export function fetchBooks(): Promise<BookMeta[]> {
  return json('/books');
}

/** Start a background ingestion job; poll `fetchIngestJob` for progress. */
export async function uploadBook(
  file: File,
  title: string,
  author: string,
): Promise<IngestJobStatus> {
  const form = new FormData();
  form.append('file', file);
  form.append('title', title);
  form.append('author', author);
  // No Content-Type header: the browser sets the multipart boundary itself.
  const res = await fetch(`${BASE}/books`, { method: 'POST', body: form });
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  return res.json() as Promise<IngestJobStatus>;
}

export function fetchIngestJob(jobId: string): Promise<IngestJobStatus> {
  return json(`/books/jobs/${jobId}`);
}

export function deleteBook(id: number): Promise<{ status: string }> {
  return json(`/books/${id}`, { method: 'DELETE' });
}

// ---- Sessions ----

export function fetchSessions(): Promise<SessionMeta[]> {
  return json('/sessions');
}

export function fetchSession(id: string): Promise<SessionDetail> {
  return json(`/sessions/${id}`);
}

export function renameSession(id: string, title: string): Promise<SessionMeta> {
  return json(`/sessions/${id}`, jsonBody('PATCH', { title }));
}

export function deleteSession(id: string): Promise<{ status: string }> {
  return json(`/sessions/${id}`, { method: 'DELETE' });
}

export function fetchOutline(bookId: number): Promise<Chapter[]> {
  return json(`/books/${bookId}/outline`);
}

export function fetchChunk(chunkId: number, window = 2): Promise<Chunk & { context?: string }> {
  return json(`/chunks/${chunkId}?window=${window}`);
}

/**
 * Stream a chat turn over SSE, invoking `onEvent` for each parsed event.
 * Returns when the stream closes. Pass an AbortSignal to cancel in flight.
 */
export async function sendChatStream(
  req: ChatRequest,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await res.text().catch(() => ''));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // SSE frames are separated by a blank line; each `data:` line is JSON.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of frame.split('\n')) {
        const trimmed = line.trimStart();
        if (!trimmed.startsWith('data:')) continue;
        const data = trimmed.slice(5).trim();
        if (!data) continue;
        try {
          onEvent(JSON.parse(data) as StreamEvent);
        } catch {
          // ignore malformed frame
        }
      }
    }
  }
}
