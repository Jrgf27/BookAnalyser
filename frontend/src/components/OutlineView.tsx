import { useEffect, useState } from 'react';
import type { BookMeta, Chapter } from '../types';
import { fetchOutline, ApiError } from '../api';

interface Props {
  book: BookMeta;
}

/** Chapter-by-chapter outline for a single scoped book, with LLM summaries. */
export default function OutlineView({ book }: Props) {
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setChapters(null);
    setError(null);
    fetchOutline(book.id)
      .then(setChapters)
      .catch((err) => {
        setError(
          err instanceof ApiError && err.status === 404
            ? 'No chapter outline is available for this book.'
            : "Sorry, the outline couldn't be loaded.",
        );
      });
  }, [book.id]);

  return (
    <div style={{ padding: 24, color: '#555' }}>
      <div style={{ fontWeight: 600, fontSize: 16 }}>{book.title}</div>
      <div style={{ fontSize: 13, color: '#888', marginBottom: 8 }}>
        {book.author} · {book.chapter_count} chapters · {book.word_count.toLocaleString()} words
      </div>
      {book.summary && (
        <div style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 20 }}>{book.summary}</div>
      )}

      <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', color: '#999', letterSpacing: 0.5, marginBottom: 8 }}>
        Chapter outline
      </div>

      {error && (
        <div style={{ background: '#f5f5f5', border: '1px solid #e0e0e0', borderRadius: 8, padding: 12, color: '#666', fontSize: 14 }}>
          {error}
        </div>
      )}

      {!chapters && !error && (
        <div style={{ color: '#888', fontStyle: 'italic', fontSize: 14 }}>Loading outline…</div>
      )}

      {chapters?.map((ch) => (
        <div key={ch.id} style={{ marginBottom: 14, paddingBottom: 14, borderBottom: '1px solid #eee' }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            {ch.number}. {ch.title}
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.5, color: ch.summary ? '#555' : '#aaa', marginTop: 2 }}>
            {ch.summary || 'No summary available.'}
          </div>
        </div>
      ))}
    </div>
  );
}
