-- Migration 011: Multi-author/narrator normalization
-- Creates normalized author/narrator tables with many-to-many junction tables
-- following the existing pattern (audiobook_genres, audiobook_eras, audiobook_topics).
--
-- Rollback: DROP TABLE IF EXISTS book_narrators; DROP TABLE IF EXISTS book_authors;
--           DROP TABLE IF EXISTS narrators; DROP TABLE IF EXISTS authors;

-- Lookup tables for unique author/narrator names
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS narrators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

-- Junction tables linking books to authors/narrators (many-to-many)
CREATE TABLE IF NOT EXISTS book_authors (
    book_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id),
    FOREIGN KEY (book_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS book_narrators (
    book_id INTEGER NOT NULL,
    narrator_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, narrator_id),
    FOREIGN KEY (book_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (narrator_id) REFERENCES narrators(id) ON DELETE CASCADE
);

-- Indices for efficient joins and sort queries
CREATE INDEX IF NOT EXISTS idx_authors_sort ON authors(sort_name);
CREATE INDEX IF NOT EXISTS idx_narrators_sort ON narrators(sort_name);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_book_narrators_narrator ON book_narrators(narrator_id);
