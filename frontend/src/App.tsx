import { useState, useEffect, useCallback } from 'react';
import type {
  BookMeta,
  ChatResponse,
  IngestJobStatus,
  SessionMeta,
  StoredMessage,
} from './types';
import {
  fetchBooks,
  fetchSessions,
  fetchSession,
  renameSession,
  deleteSession,
  uploadBook,
  fetchIngestJob,
  importSessionsDb,
} from './api';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
import ChatPane from './components/ChatPane';
import Sidebar from './components/Sidebar';
import SourceDrawer from './components/SourceDrawer';
import ScopeSelector from './components/ScopeSelector';
import TracePanel from './components/TracePanel';
import BookManager from './components/BookManager';
import OutlineView from './components/OutlineView';

export default function App() {
  const [books, setBooks] = useState<BookMeta[]>([]);
  const [selectedBookId, setSelectedBookId] = useState<number | null>(null);
  const [drawerChunkId, setDrawerChunkId] = useState<number | null>(null);
  // Bumped on every citation click so the drawer refetches even when the same
  // citation is clicked twice (e.g. after restoring a book it now exists again).
  const [drawerNonce, setDrawerNonce] = useState(0);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);

  const openCitation = (chunkId: number) => {
    setDrawerChunkId(chunkId);
    setDrawerNonce((n) => n + 1);
  };

  // Session state
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [initialMessages, setInitialMessages] = useState<StoredMessage[]>([]);
  const [showBookManager, setShowBookManager] = useState(false);
  // Ingestion job lives here (not in the modal) so its progress survives the
  // modal being closed and reopened while a book is still ingesting.
  const [ingestJob, setIngestJob] = useState<IngestJobStatus | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);
  // Mount key for ChatPane. Changes only on explicit New-chat / session-select —
  // NOT when the server assigns an id mid-stream, so an in-flight turn survives.
  const [chatKey, setChatKey] = useState('new-0');

  const refreshBooks = useCallback(() => {
    fetchBooks().then(setBooks).catch(console.error);
  }, []);

  // Upload + poll to completion. Returns whether it succeeded so the modal can
  // clear its form; runs to completion regardless of whether the modal is open.
  const startIngest = useCallback(
    async (file: File, title: string, author: string): Promise<boolean> => {
      setIngestError(null);
      let status: IngestJobStatus;
      try {
        status = await uploadBook(file, title, author);
      } catch (err) {
        setIngestError(err instanceof Error ? err.message : 'Upload failed');
        return false;
      }
      setIngestJob(status);
      try {
        while (status.status !== 'done' && status.status !== 'error') {
          await sleep(800);
          status = await fetchIngestJob(status.id);
          setIngestJob(status);
        }
        if (status.status === 'error') {
          throw new Error(status.error || 'Ingestion failed');
        }
        return true;
      } catch (err) {
        setIngestError(err instanceof Error ? err.message : 'Ingestion failed');
        return false;
      } finally {
        setIngestJob(null);
        refreshBooks();
      }
    },
    [refreshBooks],
  );

  const refreshSessions = useCallback(() => {
    fetchSessions().then(setSessions).catch(console.error);
  }, []);

  useEffect(() => {
    refreshBooks();
    refreshSessions();
  }, [refreshBooks, refreshSessions]);

  const startNewChat = () => {
    setDrawerChunkId(null);
    setLastResponse(null);
    setInitialMessages([]);
    setActiveSessionId(null);
    setChatKey(`new-${Date.now()}`);
  };

  const selectSession = async (id: string) => {
    if (id === activeSessionId) return;
    try {
      const detail = await fetchSession(id);
      setInitialMessages(detail.messages);
      setSelectedBookId(detail.book_id);
      setActiveSessionId(id);
      setDrawerChunkId(null);
      setLastResponse(null);
      setChatKey(id);
    } catch (err) {
      console.error(err);
    }
  };

  const handleRename = async (id: string, title: string) => {
    await renameSession(id, title).catch(console.error);
    refreshSessions();
  };

  const handleDelete = async (id: string) => {
    await deleteSession(id).catch(console.error);
    if (id === activeSessionId) startNewChat();
    refreshSessions();
  };

  // A brand-new chat's id is assigned by the server on the first message.
  const handleSessionCreated = (id: string) => {
    setActiveSessionId(id);
    refreshSessions();
  };

  const importSessions = async (file: File) => {
    if (!window.confirm('This replaces your entire chat history with the uploaded database. Continue?')) return;
    try {
      await importSessionsDb(file);
      startNewChat();     // the current session may no longer exist
      refreshSessions();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Import failed');
    }
  };

  // If the currently-scoped book was removed, fall back to "All books".
  useEffect(() => {
    if (selectedBookId !== null && !books.some((b) => b.id === selectedBookId)) {
      setSelectedBookId(null);
    }
  }, [books, selectedBookId]);

  // Book summaries shown in the empty-state panel to help editors orient.
  const scopedBooks =
    selectedBookId === null ? books : books.filter((b) => b.id === selectedBookId);

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={selectSession}
        onNew={startNewChat}
        onRename={handleRename}
        onDelete={handleDelete}
        onImport={importSessions}
      />

      <div style={{ display: 'flex', height: '100vh', flexDirection: 'column', flex: 1 }}>
        <header style={{ padding: '12px 24px', borderBottom: '1px solid #e0e0e0', display: 'flex', alignItems: 'center', gap: 16 }}>
          <h1 style={{ fontSize: 20, fontWeight: 600 }}>Book Assistant</h1>
          <ScopeSelector
            books={books}
            selectedBookId={selectedBookId}
            onSelect={setSelectedBookId}
          />
          <button
            onClick={() => setShowBookManager(true)}
            style={{ marginLeft: 'auto', padding: '6px 14px', borderRadius: 6, border: '1px solid #ccc', background: '#fff', cursor: 'pointer', fontSize: 14 }}
          >
            Manage books
          </button>
        </header>

        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <ChatPane
              key={chatKey}
              sessionId={activeSessionId}
              initialMessages={initialMessages}
              bookId={selectedBookId}
              onCitationClick={openCitation}
              onResponse={setLastResponse}
              onSessionCreated={handleSessionCreated}
              onTurnComplete={refreshSessions}
            />
          </div>

          <div style={{ width: 400, borderLeft: '1px solid #e0e0e0', overflow: 'auto' }}>
            {drawerChunkId !== null ? (
              <SourceDrawer
                key={drawerNonce}
                chunkId={drawerChunkId}
                onClose={() => setDrawerChunkId(null)}
              />
            ) : lastResponse?.trace.length ? (
              <TracePanel trace={lastResponse.trace} books={books} />
            ) : selectedBookId !== null && scopedBooks[0] ? (
              <OutlineView book={scopedBooks[0]} />
            ) : (
              <div style={{ padding: 24, color: '#555' }}>
                <p style={{ marginBottom: 16, color: '#888' }}>
                  Click a citation to view source context, ask a question, or pick a
                  single book above to see its chapter outline.
                </p>
                {scopedBooks.map((b) => (
                  <div key={b.id} style={{ marginBottom: 20 }}>
                    <div style={{ fontWeight: 600 }}>{b.title}</div>
                    <div style={{ fontSize: 13, color: '#888', marginBottom: 4 }}>
                      {b.author} · {b.chapter_count} chapters · {b.word_count.toLocaleString()} words
                    </div>
                    {b.summary && (
                      <div style={{ fontSize: 14, lineHeight: 1.5 }}>{b.summary}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {showBookManager && (
        <BookManager
          books={books}
          job={ingestJob}
          error={ingestError}
          onUpload={startIngest}
          onClose={() => setShowBookManager(false)}
          onChanged={refreshBooks}
        />
      )}
    </div>
  );
}
