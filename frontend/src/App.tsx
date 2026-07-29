import React, { useState, useEffect } from 'react';
import type { BookMeta, ChatResponse } from './types';
import { fetchBooks } from './api';
import ChatPane from './components/ChatPane';
import SourceDrawer from './components/SourceDrawer';
import ScopeSelector from './components/ScopeSelector';
import TracePanel from './components/TracePanel';

export default function App() {
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [selectedBookId, setSelectedBookId] = useState<number | null>(null);
  const [drawerChunkId, setDrawerChunkId] = useState<number | null>(null);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);

  useEffect(() => {
    fetchBooks().then(setBooks).catch(console.error);
  }, []);

  return (
    <div style={{ display: 'flex', height: '100vh', flexDirection: 'column' }}>
      <header style={{ padding: '12px 24px', borderBottom: '1px solid #e0e0e0', display: 'flex', alignItems: 'center', gap: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600 }}>Book Assistant</h1>
        <ScopeSelector
          books={books}
          selectedBookId={selectedBookId}
          onSelect={setSelectedBookId}
        />
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <ChatPane
            bookId={selectedBookId}
            onCitationClick={setDrawerChunkId}
            onResponse={setLastResponse}
          />
        </div>

        <div style={{ width: 400, borderLeft: '1px solid #e0e0e0', overflow: 'auto' }}>
          {drawerChunkId !== null ? (
            <SourceDrawer
              chunkId={drawerChunkId}
              onClose={() => setDrawerChunkId(null)}
            />
          ) : lastResponse?.trace.length ? (
            <TracePanel trace={lastResponse.trace} />
          ) : (
            <div style={{ padding: 24, color: '#888' }}>
              Click a citation to view source context, or ask a question to get started.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
