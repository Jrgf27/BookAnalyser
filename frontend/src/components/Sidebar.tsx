import React from 'react';
import type { SessionMeta } from '../types';

interface Props {
  sessions: SessionMeta[];
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onImport: (file: File) => void;
}

export default function Sidebar({
  sessions,
  activeSessionId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onImport,
}: Props) {
  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (f) onImport(f);
  };
  return (
    <div
      style={{
        width: 250,
        borderRight: '1px solid #e0e0e0',
        display: 'flex',
        flexDirection: 'column',
        background: '#fafafa',
      }}
    >
      <div style={{ padding: 12 }}>
        <div
          style={{
            display: 'flex',
            gap: 8,
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingBottom: 12,
            marginBottom: 12,
            borderBottom: '1px solid #e4e4e4',
          }}
        >
          <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, color: '#999' }}>
            Chat history
          </span>
          <span style={{ display: 'flex', gap: 4 }}>
            <a
              href="/api/export/sessions.db"
              download
              title="Download your chat history (sessions.db)"
              style={ghostBtn}
            >
              ↓ Download
            </a>
            <label title="Restore chat history from a sessions.db file" style={ghostBtn}>
              ↑ Restore
              <input type="file" accept=".db,application/x-sqlite3" onChange={handleImport} hidden />
            </label>
          </span>
        </div>
        <button onClick={onNew} style={newChatBtn}>
          + New chat
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px' }}>
        {sessions.length === 0 && (
          <div style={{ padding: 12, color: '#aaa', fontSize: 13 }}>
            No conversations yet.
          </div>
        )}
        {sessions.map((s) => {
          const active = s.id === activeSessionId;
          return (
            <div
              key={s.id}
              onClick={() => onSelect(s.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                padding: '8px 10px',
                marginBottom: 2,
                borderRadius: 6,
                cursor: 'pointer',
                background: active ? '#e3f2fd' : 'transparent',
              }}
            >
              <span
                style={{
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontSize: 14,
                }}
                title={s.title}
              >
                {s.title}
              </span>
              <button
                title="Rename"
                onClick={(e) => {
                  e.stopPropagation();
                  const next = window.prompt('Rename conversation', s.title);
                  if (next && next.trim()) onRename(s.id, next.trim());
                }}
                style={iconBtn}
              >
                ✎
              </button>
              <button
                title="Delete"
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm(`Delete "${s.title}"?`)) onDelete(s.id);
                }}
                style={iconBtn}
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  border: 'none',
  background: 'transparent',
  cursor: 'pointer',
  color: '#888',
  fontSize: 13,
  padding: '2px 4px',
  lineHeight: 1,
};

const ghostBtn: React.CSSProperties = {
  fontSize: 12,
  color: '#1976d2',
  textDecoration: 'none',
  cursor: 'pointer',
  padding: '2px 6px',
  borderRadius: 4,
  whiteSpace: 'nowrap',
};

const newChatBtn: React.CSSProperties = {
  width: '100%',
  padding: '9px 12px',
  borderRadius: 6,
  border: 'none',
  background: '#1976d2',
  color: '#fff',
  cursor: 'pointer',
  fontSize: 14,
  fontWeight: 500,
};
