import React, { useState, useRef, useEffect } from 'react';
import type { ChatResponse } from '../types';
import { sendChat } from '../api';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface Props {
  bookId: number | null;
  onCitationClick: (chunkId: number) => void;
  onResponse: (response: ChatResponse) => void;
}

/** Regex for citation markers like [pp:12:347] */
const CITE_RE = /\[([a-z]+:\d+:\d+)\]/g;

function renderWithCitations(
  text: string,
  onClick: (chunkId: number) => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  const regex = new RegExp(CITE_RE);
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const raw = match[1]; // e.g. "pp:12:347"
    const chunkId = parseInt(raw.split(':')[2], 10);
    parts.push(
      <button
        key={match.index}
        onClick={() => onClick(chunkId)}
        style={{
          display: 'inline',
          background: '#e3f2fd',
          border: '1px solid #90caf9',
          borderRadius: 4,
          padding: '1px 6px',
          fontSize: '0.85em',
          cursor: 'pointer',
          fontFamily: 'monospace',
        }}
      >
        {raw}
      </button>,
    );
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

export default function ChatPane({ bookId, onCitationClick, onResponse }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    setLoading(true);

    try {
      const response = await sendChat({ message: text, book_id: bookId });
      setMessages((prev) => [...prev, { role: 'assistant', content: response.answer }]);
      onResponse(response);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${err instanceof Error ? err.message : 'unknown'}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {messages.map((msg, i) => (
          <div
            key={i}
            style={{
              marginBottom: 16,
              padding: 12,
              borderRadius: 8,
              background: msg.role === 'user' ? '#f5f5f5' : '#fff',
              border: msg.role === 'assistant' ? '1px solid #e0e0e0' : 'none',
              whiteSpace: 'pre-wrap',
            }}
          >
            <strong>{msg.role === 'user' ? 'You' : 'Assistant'}</strong>
            <div style={{ marginTop: 4 }}>
              {msg.role === 'assistant'
                ? renderWithCitations(msg.content, onCitationClick)
                : msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ color: '#888', fontStyle: 'italic' }}>Thinking...</div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{ padding: '12px 24px', borderTop: '1px solid #e0e0e0', display: 'flex', gap: 8 }}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          placeholder="Ask about Little Women or Pride & Prejudice..."
          style={{ flex: 1, padding: '8px 12px', borderRadius: 6, border: '1px solid #ccc', fontSize: 14 }}
          disabled={loading}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          style={{ padding: '8px 20px', borderRadius: 6, border: 'none', background: '#1976d2', color: '#fff', cursor: 'pointer', fontSize: 14 }}
        >
          Send
        </button>
      </div>
    </>
  );
}
