import React, { useEffect, useState } from 'react';
import { fetchChunk } from '../api';

interface Props {
  chunkId: number;
  onClose: () => void;
}

interface ChunkWithContext {
  id: number;
  book_key?: string;
  chapter_number: number;
  text: string;
  char_start: number;
  char_end: number;
  context?: string;
  context_char_start?: number;
  context_char_end?: number;
}

/**
 * Highlights the cited chunk span within the broader chapter context.
 */
function highlightContext(chunk: ChunkWithContext): React.ReactNode {
  if (!chunk.context || chunk.context_char_start === undefined) {
    return <p>{chunk.text}</p>;
  }

  const ctxStart = chunk.context_char_start;
  const relStart = chunk.char_start - ctxStart;
  const relEnd = chunk.char_end - ctxStart;
  const ctx = chunk.context;

  const before = ctx.slice(0, relStart);
  const highlighted = ctx.slice(relStart, relEnd);
  const after = ctx.slice(relEnd);

  return (
    <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
      <span style={{ color: '#666' }}>{before}</span>
      <mark style={{ background: '#fff9c4', padding: '2px 0' }}>{highlighted}</mark>
      <span style={{ color: '#666' }}>{after}</span>
    </div>
  );
}

export default function SourceDrawer({ chunkId, onClose }: Props) {
  const [chunk, setChunk] = useState<ChunkWithContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setChunk(null);
    setError(null);
    fetchChunk(chunkId, 2)
      .then((data) => setChunk(data as unknown as ChunkWithContext))
      .catch((err) => setError(err.message));
  }, [chunkId]);

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600 }}>
          Source — Chunk #{chunkId}
        </h3>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18 }}
        >
          &times;
        </button>
      </div>

      {error && <p style={{ color: 'red' }}>{error}</p>}

      {chunk && (
        <>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
            {chunk.book_key?.toUpperCase()} &middot; Chapter {chunk.chapter_number}
          </div>
          <div style={{ fontSize: 14, border: '1px solid #e0e0e0', borderRadius: 8, padding: 12 }}>
            {highlightContext(chunk)}
          </div>
        </>
      )}

      {!chunk && !error && (
        <p style={{ color: '#888', fontStyle: 'italic' }}>Loading...</p>
      )}
    </div>
  );
}
