-- Audiobook Library Database Schema
-- SQLite database with full-text search and indices for fast queries

CREATE TABLE IF NOT EXISTS audiobooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    author_last_name TEXT,        -- Extracted last name for sorting
    author_first_name TEXT,       -- Extracted first name for sorting
    narrator TEXT,
    narrator_last_name TEXT,      -- Extracted last name for sorting
    narrator_first_name TEXT,     -- Extracted first name for sorting
    publisher TEXT,
    series TEXT,
    series_sequence REAL,         -- Position in series (1, 2, 3.5, etc.)
    edition TEXT,                 -- Edition info (1st, 2nd, Anniversary, etc.)
    asin TEXT,                    -- Amazon Standard Identification Number
    isbn TEXT,                    -- International Standard Book Number
    source TEXT DEFAULT 'audible', -- Source: audible, google_play, librivox, chirp, libro_fm
    content_type TEXT DEFAULT 'Product', -- Audible content type: Product, Podcast, Lecture, Performance, Speech, Radio/TV Program
    source_asin TEXT,             -- Original Audible ASIN for cross-referencing
    duration_hours REAL,
    duration_formatted TEXT,
    file_size_mb REAL,
    file_path TEXT UNIQUE NOT NULL,
    cover_path TEXT,
    format TEXT,
    quality TEXT,
    published_year INTEGER,
    published_date TEXT,          -- Full publish date if available (YYYY-MM-DD)
    acquired_date TEXT,           -- When the audiobook was added to library
    description TEXT,
    sha256_hash TEXT,
    hash_verified_at TIMESTAMP,
    -- Playback position tracking (Audible sync)
    playback_position_ms INTEGER DEFAULT 0,
    playback_position_updated TIMESTAMP,
    audible_position_ms INTEGER,
    audible_position_updated TIMESTAMP,
    position_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS genres (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS audiobook_genres (
    audiobook_id INTEGER,
    genre_id INTEGER,
    PRIMARY KEY (audiobook_id, genre_id),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (genre_id) REFERENCES genres(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS audiobook_eras (
    audiobook_id INTEGER,
    era_id INTEGER,
    PRIMARY KEY (audiobook_id, era_id),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (era_id) REFERENCES eras(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS supplements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER,
    type TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT UNIQUE NOT NULL,
    file_size_mb REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_supplements_audiobook ON supplements(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_supplements_type ON supplements(type);

CREATE TABLE IF NOT EXISTS audiobook_topics (
    audiobook_id INTEGER,
    topic_id INTEGER,
    PRIMARY KEY (audiobook_id, topic_id),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE
);

-- Full-text search virtual table for fast text search
CREATE VIRTUAL TABLE IF NOT EXISTS audiobooks_fts USING fts5(
    title,
    author,
    narrator,
    publisher,
    series,
    description,
    content=audiobooks,
    content_rowid=id
);

-- Triggers to keep FTS table in sync
CREATE TRIGGER IF NOT EXISTS audiobooks_ai AFTER INSERT ON audiobooks BEGIN
    INSERT INTO audiobooks_fts(rowid, title, author, narrator, publisher, series, description)
    VALUES (new.id, new.title, new.author, new.narrator, new.publisher, new.series, new.description);
END;

CREATE TRIGGER IF NOT EXISTS audiobooks_ad AFTER DELETE ON audiobooks BEGIN
    DELETE FROM audiobooks_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS audiobooks_au AFTER UPDATE ON audiobooks BEGIN
    UPDATE audiobooks_fts
    SET title = new.title,
        author = new.author,
        narrator = new.narrator,
        publisher = new.publisher,
        series = new.series,
        description = new.description
    WHERE rowid = new.id;
END;

-- Indices for fast queries
CREATE INDEX IF NOT EXISTS idx_audiobooks_title ON audiobooks(title);
CREATE INDEX IF NOT EXISTS idx_audiobooks_author ON audiobooks(author);
CREATE INDEX IF NOT EXISTS idx_audiobooks_narrator ON audiobooks(narrator);
CREATE INDEX IF NOT EXISTS idx_audiobooks_publisher ON audiobooks(publisher);
CREATE INDEX IF NOT EXISTS idx_audiobooks_series ON audiobooks(series);
CREATE INDEX IF NOT EXISTS idx_audiobooks_format ON audiobooks(format);
CREATE INDEX IF NOT EXISTS idx_audiobooks_duration ON audiobooks(duration_hours);
CREATE INDEX IF NOT EXISTS idx_audiobooks_year ON audiobooks(published_year);
CREATE INDEX IF NOT EXISTS idx_audiobooks_sha256 ON audiobooks(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_audiobooks_content_type ON audiobooks(content_type);

-- View for easy querying with all related data
CREATE VIEW IF NOT EXISTS audiobooks_full AS
SELECT
    a.id,
    a.title,
    a.author,
    a.narrator,
    a.publisher,
    a.series,
    a.duration_hours,
    a.duration_formatted,
    a.file_size_mb,
    a.file_path,
    a.cover_path,
    a.format,
    a.quality,
    a.published_year,
    a.description,
    a.sha256_hash,
    a.hash_verified_at,
    a.content_type,
    a.created_at,
    GROUP_CONCAT(DISTINCT g.name) as genres,
    GROUP_CONCAT(DISTINCT e.name) as eras,
    GROUP_CONCAT(DISTINCT t.name) as topics
FROM audiobooks a
LEFT JOIN audiobook_genres ag ON a.id = ag.audiobook_id
LEFT JOIN genres g ON ag.genre_id = g.id
LEFT JOIN audiobook_eras ae ON a.id = ae.audiobook_id
LEFT JOIN eras e ON ae.era_id = e.id
LEFT JOIN audiobook_topics at ON a.id = at.audiobook_id
LEFT JOIN topics t ON at.topic_id = t.id
GROUP BY a.id;

-- Playback position tracking
CREATE TABLE IF NOT EXISTS playback_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    position_ms INTEGER NOT NULL,
    source TEXT NOT NULL,  -- 'local', 'audible', 'sync'
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_playback_history_audiobook ON playback_history(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_playback_history_recorded ON playback_history(recorded_at);
CREATE INDEX IF NOT EXISTS idx_audiobooks_position ON audiobooks(playback_position_ms);
CREATE INDEX IF NOT EXISTS idx_audiobooks_asin_position ON audiobooks(asin, playback_position_ms);

-- View for books with Audible sync capability (have ASIN)
CREATE VIEW IF NOT EXISTS audiobooks_syncable AS
SELECT
    id,
    title,
    author,
    asin,
    duration_hours,
    playback_position_ms,
    playback_position_updated,
    audible_position_ms,
    audible_position_updated,
    position_synced_at,
    CASE
        WHEN duration_hours > 0 THEN
            ROUND(CAST(playback_position_ms AS REAL) / (duration_hours * 3600000) * 100, 1)
        ELSE 0
    END as percent_complete
FROM audiobooks
WHERE asin IS NOT NULL AND asin != '';

-- View for main library that excludes periodical content types
-- Used by AUDIOBOOK_FILTER to separate main library from Reading Room
CREATE VIEW IF NOT EXISTS library_audiobooks AS
SELECT * FROM audiobooks
WHERE content_type IN ('Product', 'Lecture', 'Performance', 'Speech') OR content_type IS NULL;
