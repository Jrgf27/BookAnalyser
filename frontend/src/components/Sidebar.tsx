import React from 'react';
import type { SessionMeta } from '../types';

interface Props {
  sessions: SessionMeta[];
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export default function Sidebar({
  sessions,
  activeSessionId,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: Props) {
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
        <button
          onClick={onNew}
          style={{
            width: '100%',
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid #1976d2',
            background: '#1976d2',
            color: '#fff',
            cursor: 'pointer',
            fontSize: 14,
          }}
        >
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
