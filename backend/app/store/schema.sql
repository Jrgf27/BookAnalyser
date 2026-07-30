-- DDL for books.db — idempotent (uses IF NOT EXISTS everywhere)

CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    author      TEXT    NOT NULL,
    key         TEXT    NOT NULL UNIQUE,   -- short slug: 'lw', 'pp'
    word_count  INTEGER NOT NULL DEFAULT 0,
    chapter_count INTEGER NOT NULL DEFAULT 0,
    summary     TEXT,
    ready       INTEGER NOT NULL DEFAULT 0,  -- 1 only after full ingestion
    UNIQUE(title, author)                    -- no two books with the same title+author
);

CREATE TABLE IF NOT EXISTS chapters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    number      INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    text        TEXT    NOT NULL,          -- full chapter text
    summary     TEXT,
    word_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(book_id, number)
);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id         INTEGER NOT NULL REFERENCES books(id),
    chapter_id      INTEGER NOT NULL REFERENCES chapters(id),
    chapter_number  INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    char_start      INTEGER NOT NULL,     -- offset into chapters.text
    char_end        INTEGER NOT NULL,
    token_count     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_book    ON chunks(book_id);
CREATE INDEX IF NOT EXISTS idx_chunks_chapter ON chunks(chapter_id);

-- FTS5 full-text index (content-sync'd with chunks table)
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

-- sqlite-vec vector index (created at runtime after extension is loaded)
-- CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
--     book_id     INTEGER PARTITION KEY,
--     chapter_number INTEGER,
--     embedding   FLOAT[3072] distance_metric=cosine
-- );
