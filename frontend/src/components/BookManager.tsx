import React, { useRef, useState } from 'react';
import type { BookMeta, IngestJobStatus } from '../types';
import { deleteBook, importBooksDb } from '../api';

interface Props {
  books: BookMeta[];
  jobs: IngestJobStatus[];       // in-flight ingestions (incl. seeds), owned by App
  error: string | null;          // ingestion error, owned by App
  onUpload: (file: File, title: string, author: string) => Promise<boolean>;
  onClose: () => void;
  onChanged: () => void; // refresh the book list after delete
}

export default function BookManager({
  books,
  jobs,
  error,
  onUpload,
  onClose,
  onChanged,
}: Props) {
  const [title, setTitle] = useState('');
  const [author, setAuthor] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // An ingestion is in flight (this user's upload, or the startup seed). New
  // uploads are still allowed — the backend serializes them behind a lock, so
  // they just queue — but destructive actions (delete) wait until it's idle.
  const busy = jobs.length > 0;
  const canUpload = !!file && !!title.trim();

  const handleUpload = async () => {
    if (!file || !title.trim()) return;
    const ok = await onUpload(file, title.trim(), author.trim());
    if (ok) {
      setTitle('');
      setAuthor('');
      setFile(null);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleDelete = async (b: BookMeta) => {
    if (!window.confirm(`Remove "${b.title}" from the library?`)) return;
    setDeleteError(null);
    try {
      await deleteBook(b.id);
      onChanged();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  const handleRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    if (!window.confirm('This replaces your entire current library with the uploaded database. Continue?')) return;
    setDeleteError(null);
    try {
      await importBooksDb(f);
      onChanged();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Restore failed');
    }
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 10,
          width: 560,
          maxWidth: '90vw',
          maxHeight: '85vh',
          overflow: 'auto',
          padding: 24,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ fontSize: 18, fontWeight: 600, flex: 1 }}>Manage books</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'transparent', fontSize: 20, cursor: 'pointer', color: '#888' }}>
            ✕
          </button>
        </div>

        {(error || deleteError) && (
          <div style={{ background: '#ffebee', color: '#c62828', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 13 }}>
            {error || deleteError}
          </div>
        )}

        {/* Current library */}
        <div style={{ marginBottom: 24 }}>
          {books.length === 0 && (
            <div style={{ color: '#aaa', fontSize: 14 }}>No books loaded yet.</div>
          )}
          {books.map((b) => (
            <div
              key={b.id}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0', borderBottom: '1px solid #eee' }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{b.title}</div>
                <div style={{ fontSize: 12, color: '#888' }}>
                  {b.author} · {b.chapter_count} chapters · {b.word_count.toLocaleString()} words
                </div>
              </div>
              <button
                onClick={() => handleDelete(b)}
                disabled={busy}
                style={{ border: '1px solid #e0a0a0', background: '#fff', color: '#c62828', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 13 }}
              >
                Remove
              </button>
            </div>
          ))}
        </div>

        {/* Upload */}
        <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Add a book (HTML)</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input
            type="text"
            placeholder="Title (required)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={inputStyle}
          />
          <input
            type="text"
            placeholder="Author"
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            style={inputStyle}
          />
          <input
            ref={fileRef}
            type="file"
            accept=".html,.htm,text/html"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            style={{ fontSize: 14 }}
          />
          <button
            onClick={handleUpload}
            disabled={!canUpload}
            style={{
              padding: '8px 16px',
              borderRadius: 6,
              border: 'none',
              background: canUpload ? '#1976d2' : '#b0bec5',
              color: '#fff',
              cursor: canUpload ? 'pointer' : 'default',
              fontSize: 14,
              alignSelf: 'flex-start',
            }}
          >
            {busy ? 'Add to queue' : 'Upload & ingest'}
          </button>

          {jobs.map((job) => (
            <div key={job.id} style={{ marginTop: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#444', marginBottom: 2 }}>
                {job.title}
              </div>
              <div style={{ height: 8, background: '#eee', borderRadius: 4, overflow: 'hidden' }}>
                <div
                  style={{
                    height: '100%',
                    width: `${Math.round(job.progress * 100)}%`,
                    background: '#1976d2',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
              <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
                {job.status === 'queued'
                  ? 'Queued — waiting for another ingestion to finish'
                  : `${job.stage ? `${job.stage} — ` : ''}${job.detail || 'Starting…'} (${Math.round(job.progress * 100)}%)`}
              </div>
            </div>
          ))}
        </div>

        {/* Backup / restore */}
        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '24px 0 4px' }}>Back up your library</h3>
        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
          Download your book database to keep it, or restore one you saved earlier.
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <a href="/api/export/books.db" download style={secondaryBtn}>
            ↓ Download
          </a>
          <label style={secondaryBtn}>
            ↑ Restore
            <input type="file" accept=".db,application/x-sqlite3" onChange={handleRestore} hidden />
          </label>
        </div>
      </div>
    </div>
  );
}

const secondaryBtn: React.CSSProperties = {
  padding: '8px 14px',
  borderRadius: 6,
  border: '1px solid #cfd8dc',
  background: '#fff',
  color: '#1976d2',
  fontSize: 13,
  fontWeight: 500,
  textDecoration: 'none',
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
};

const inputStyle: React.CSSProperties = {
  padding: '8px 12px',
  borderRadius: 6,
  border: '1px solid #ccc',
  fontSize: 14,
};
