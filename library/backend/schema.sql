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
    chapter_count INTEGER,        -- Populated by scanner at ingest; lazy-backfilled on first streaming request
    file_size_mb REAL,
    file_path TEXT UNIQUE NOT NULL,
    cover_path TEXT,
    format TEXT,
    quality TEXT,
    published_year INTEGER,
    published_date TEXT,          -- Full publish date if available (YYYY-MM-DD)
    acquired_date TEXT,           -- When the audiobook was added to library
    description TEXT,
    -- Audible enrichment fields
    subtitle TEXT,
    language TEXT,
    format_type TEXT,                   -- Unabridged, Abridged, Original Recording
    runtime_length_min INTEGER,         -- Audible's duration in minutes
    release_date TEXT,                  -- Audible release date (may differ from published_date)
    publisher_summary TEXT,             -- Audible's HTML publisher summary
    rating_overall REAL,
    rating_performance REAL,
    rating_story REAL,
    num_ratings INTEGER,
    num_reviews INTEGER,
    audible_image_url TEXT,             -- Cover art URL from Audible
    sample_url TEXT,                    -- Audio sample URL
    audible_sku TEXT,                   -- Audible SKU identifier
    is_adult_product INTEGER DEFAULT 0,
    merchandising_summary TEXT,
    audible_enriched_at TIMESTAMP,      -- When metadata was last pulled from Audible
    isbn_enriched_at TIMESTAMP,         -- When metadata was last pulled from ISBN source
    enrichment_source TEXT,             -- Which provider enriched: local, audible, google_books, openlibrary
    -- Integrity
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

-- Audible hierarchical categories (category_ladders)
CREATE TABLE IF NOT EXISTS audible_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    category_path TEXT NOT NULL,
    category_name TEXT NOT NULL,
    root_category TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 1,
    audible_category_id TEXT,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audible_categories_audiobook ON audible_categories(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_audible_categories_root ON audible_categories(root_category);
CREATE INDEX IF NOT EXISTS idx_audible_categories_name ON audible_categories(category_name);
CREATE INDEX IF NOT EXISTS idx_audible_categories_path ON audible_categories(category_path);

-- Editorial reviews from Audible
CREATE TABLE IF NOT EXISTS editorial_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    review_text TEXT NOT NULL,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_editorial_reviews_audiobook ON editorial_reviews(audiobook_id);

CREATE TABLE IF NOT EXISTS audiobook_topics (
    audiobook_id INTEGER,
    topic_id INTEGER,
    PRIMARY KEY (audiobook_id, topic_id),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE
);

-- Normalized author/narrator tables (many-to-many)
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    asin TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS narrators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    UNIQUE(name)
);

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

CREATE INDEX IF NOT EXISTS idx_authors_sort ON authors(sort_name);
CREATE INDEX IF NOT EXISTS idx_authors_asin ON authors(asin);
CREATE INDEX IF NOT EXISTS idx_narrators_sort ON narrators(sort_name);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_book_narrators_narrator ON book_narrators(narrator_id);

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
    INSERT INTO audiobooks_fts(audiobooks_fts, rowid, title, author, narrator, publisher, series, description)
    VALUES ('delete', old.id, old.title, old.author, old.narrator, old.publisher, old.series, old.description);
END;

-- FTS5 external content tables require delete+insert, NOT update.
-- Using UPDATE on content-synced FTS5 corrupts the index silently.
CREATE TRIGGER IF NOT EXISTS audiobooks_au AFTER UPDATE ON audiobooks BEGIN
    INSERT INTO audiobooks_fts(audiobooks_fts, rowid, title, author, narrator, publisher, series, description)
    VALUES ('delete', old.id, old.title, old.author, old.narrator, old.publisher, old.series, old.description);
    INSERT INTO audiobooks_fts(rowid, title, author, narrator, publisher, series, description)
    VALUES (new.id, new.title, new.author, new.narrator, new.publisher, new.series, new.description);
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
CREATE INDEX IF NOT EXISTS idx_audiobooks_language ON audiobooks(language);
CREATE INDEX IF NOT EXISTS idx_audiobooks_format_type ON audiobooks(format_type);
CREATE INDEX IF NOT EXISTS idx_audiobooks_rating ON audiobooks(rating_overall);
CREATE INDEX IF NOT EXISTS idx_audiobooks_release_date ON audiobooks(release_date);
CREATE INDEX IF NOT EXISTS idx_audiobooks_audible_sku ON audiobooks(audible_sku);
CREATE INDEX IF NOT EXISTS idx_audiobooks_is_adult ON audiobooks(is_adult_product);
CREATE INDEX IF NOT EXISTS idx_audiobooks_enriched ON audiobooks(audible_enriched_at);

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

-- View for main library that excludes non-audiobook content types
-- Used by AUDIOBOOK_FILTER in api_modular/audiobooks.py
CREATE VIEW IF NOT EXISTS library_audiobooks AS
SELECT * FROM audiobooks
WHERE content_type IN ('Product', 'Performance', 'Speech') OR content_type IS NULL;

-- ================================================================
-- Maintenance Scheduling Tables
-- ================================================================

CREATE TABLE IF NOT EXISTS maintenance_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL,
    task_params TEXT DEFAULT '{}',
    schedule_type TEXT NOT NULL,
    cron_expression TEXT,
    scheduled_at TEXT,
    next_run_at TEXT,
    duration_minutes INTEGER DEFAULT 30,
    lead_time_hours INTEGER DEFAULT 48,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS trg_maint_windows_updated
    AFTER UPDATE ON maintenance_windows
    FOR EACH ROW
BEGIN
    UPDATE maintenance_windows SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS maintenance_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    dismissed_at TEXT,
    dismissed_by TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    result_message TEXT,
    result_data TEXT DEFAULT '{}',
    FOREIGN KEY (window_id) REFERENCES maintenance_windows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS maintenance_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    delivered INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_maint_windows_next_run ON maintenance_windows(next_run_at);
CREATE INDEX IF NOT EXISTS idx_maint_windows_status ON maintenance_windows(status);
CREATE INDEX IF NOT EXISTS idx_maint_messages_active ON maintenance_messages(dismissed_at);
CREATE INDEX IF NOT EXISTS idx_maint_history_window ON maintenance_history(window_id);
CREATE INDEX IF NOT EXISTS idx_maint_notifications_pending ON maintenance_notifications(delivered, created_at);

-- ================================================================
-- Roadmap items (admin-editable, publicly visible)
-- ================================================================
CREATE TABLE IF NOT EXISTS roadmap_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'planned',  -- planned, in_progress, completed, cancelled
    priority TEXT NOT NULL DEFAULT 'medium', -- low, medium, high
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_roadmap_status ON roadmap_items(status);
CREATE INDEX IF NOT EXISTS idx_roadmap_sort ON roadmap_items(sort_order);

-- ================================================================
-- User suggestions (comment pad from Help page)
-- ================================================================
CREATE TABLE IF NOT EXISTS user_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_suggestions_read ON user_suggestions(is_read);
CREATE INDEX IF NOT EXISTS idx_suggestions_created ON user_suggestions(created_at);

-- ================================================================
-- Audiobook translations (localized metadata per locale)
-- ================================================================
CREATE TABLE IF NOT EXISTS audiobook_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,               -- BCP 47 tag: 'zh-Hans', 'ja', 'ko', etc.
    title TEXT,                         -- Translated title
    author_display TEXT,                -- Translated/transliterated author name
    series_display TEXT,                -- Translated series name
    description TEXT,                   -- Translated description/summary
    translator TEXT,                    -- Who/what translated ('deepl', 'manual', etc.)
    pinyin_sort TEXT,                   -- Tone-stripped lowercase pinyin of title (zh sort key)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audiobook_id, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audiobook_translations_locale ON audiobook_translations(locale);
CREATE INDEX IF NOT EXISTS idx_audiobook_translations_book ON audiobook_translations(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_audiobook_translations_pinyin_sort ON audiobook_translations(locale, pinyin_sort);

-- Chapter subtitles (Phase 2: dual-language VTT files)
CREATE TABLE IF NOT EXISTS chapter_subtitles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    chapter_title TEXT,
    locale TEXT NOT NULL,
    vtt_path TEXT NOT NULL,
    stt_provider TEXT,
    translation_provider TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audiobook_id, chapter_index, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chapter_subtitles_book ON chapter_subtitles(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_chapter_subtitles_locale ON chapter_subtitles(audiobook_id, locale);

-- Translated chapter audio (Phase 3: TTS-generated audio per chapter)
CREATE TABLE IF NOT EXISTS chapter_translations_audio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    locale TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    tts_provider TEXT NOT NULL,
    tts_voice TEXT,
    duration_seconds REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audiobook_id, chapter_index, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chapter_audio_book ON chapter_translations_audio(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_chapter_audio_locale ON chapter_translations_audio(audiobook_id, locale);

-- ================================================================
-- Streaming translation segments (on-demand translation pipeline)
-- ================================================================

-- Tracks per-segment translation state for the streaming pipeline.
-- A "segment" is a 30-second slice of a chapter. The active chapter
-- streams segments back incrementally; prefetch chapters are processed
-- whole but tracked here for the segment bitmap.
CREATE TABLE IF NOT EXISTS streaming_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    locale TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',   -- pending, processing, completed, failed
    priority INTEGER NOT NULL DEFAULT 2,     -- 0=P0 cursor, 1=P1 chase, 2=sampler, 3=backlog
    worker_id TEXT,                          -- GPU worker identifier
    vtt_content TEXT,                        -- Translated VTT cues for this segment
    source_vtt_content TEXT,                 -- English (source) VTT cues for this segment
    audio_path TEXT,                         -- Path to TTS audio for this segment
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,  -- Bounded retry budget (cap = 3)
    origin TEXT NOT NULL DEFAULT 'live'      -- live | sampler | backlog — audit + invariant enforcement
        CHECK (origin IN ('live','sampler','backlog')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streaming_seg_book ON streaming_segments(audiobook_id, locale);
CREATE INDEX IF NOT EXISTS idx_streaming_seg_state ON streaming_segments(state, priority);
CREATE INDEX IF NOT EXISTS idx_streaming_seg_chapter ON streaming_segments(audiobook_id, chapter_index, locale);

-- Cross-column invariant: sampler rows MUST have priority >= 2. p0/p1 are
-- reserved EXCLUSIVELY for live-playback work on the currently-playing book.
-- SQLite doesn't support adding cross-column CHECK via ALTER TABLE, so the
-- invariant is enforced by BEFORE INSERT/UPDATE triggers that ABORT on
-- violation. Paired test in test_sampler_priority_invariant.py.
CREATE TRIGGER IF NOT EXISTS streaming_segments_sampler_priority_ins
BEFORE INSERT ON streaming_segments
WHEN NEW.origin = 'sampler' AND NEW.priority < 2
BEGIN
    SELECT RAISE(ABORT, 'sampler rows must have priority >= 2 (p0/p1 reserved for live playback)');
END;

CREATE TRIGGER IF NOT EXISTS streaming_segments_sampler_priority_upd
BEFORE UPDATE ON streaming_segments
WHEN NEW.origin = 'sampler' AND NEW.priority < 2
BEGIN
    SELECT RAISE(ABORT, 'sampler rows must have priority >= 2 (p0/p1 reserved for live playback)');
END;

-- Per-book sampler job tracking. One row per (audiobook_id, locale). Drives
-- the library-browse "Play sample" affordance (row exists AND status='complete'
-- → show affordance). Also surfaces failure state for admin retry.
CREATE TABLE IF NOT EXISTS sampler_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, running, complete, failed
    segments_target INTEGER NOT NULL,        -- how many segments the sampler aims to translate
    segments_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audiobook_id, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sampler_jobs_status ON sampler_jobs(status);
CREATE INDEX IF NOT EXISTS idx_sampler_jobs_locale ON sampler_jobs(locale, status);

-- Streaming translation sessions — tracks an active streaming request
-- from a player instance. Links a playback event to its segment work.
CREATE TABLE IF NOT EXISTS streaming_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,
    active_chapter INTEGER NOT NULL DEFAULT 0,
    buffer_threshold INTEGER NOT NULL DEFAULT 6,  -- segments before playback starts
    state TEXT NOT NULL DEFAULT 'buffering',       -- buffering, streaming, completed, cancelled, stopped, error
    gpu_warm INTEGER NOT NULL DEFAULT 0,           -- 1 if GPU is warmed up
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streaming_sess_book ON streaming_sessions(audiobook_id, locale);
CREATE INDEX IF NOT EXISTS idx_streaming_sess_state ON streaming_sessions(state);

-- Translation monitor audit trail (v8.3.9). Every action the live + sampler
-- monitors take (claim reset, retry exhausted, spend pause, etc.) is logged
-- here for operator audit. See library/backend/migrations/025_translation_monitor_events.sql
-- for the rationale and the type taxonomy. Local-only — not exported by transfer.py.
CREATE TABLE IF NOT EXISTS translation_monitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor TEXT NOT NULL CHECK (monitor IN ('live','sampler')),
    event_type TEXT NOT NULL,
    audiobook_id INTEGER,
    segment_id INTEGER,
    sampler_job_id INTEGER,
    worker_id TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tm_events_created
    ON translation_monitor_events(created_at);
CREATE INDEX IF NOT EXISTS idx_tm_events_type
    ON translation_monitor_events(event_type);
CREATE INDEX IF NOT EXISTS idx_tm_events_monitor_created
    ON translation_monitor_events(monitor, created_at);
