-- Migration: 006_periodicals.sql
-- Description: Add periodicals table for non-audiobook content from Audible library
-- Date: 2026-01-08
--
-- Content types synced from Audible library:
--   - Podcast: podcast series and episodes
--   - Newspaper / Magazine: NYT Digest, etc.
--   - Show: meditation series, interview shows
--   - Radio/TV Program: documentaries, radio dramas

-- Drop old table if exists (schema changed from parent/child model)
DROP TABLE IF EXISTS periodicals;

-- Periodicals table
-- Each row is a library item with non-Product content_type
CREATE TABLE IF NOT EXISTS periodicals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Primary identifier
    asin TEXT NOT NULL UNIQUE,            -- Audible ASIN (unique per item)

    -- Content metadata
    title TEXT NOT NULL,                  -- Item title
    author TEXT,                          -- Creator/host
    narrator TEXT,                        -- Narrator (often same as author)
    runtime_minutes INTEGER,              -- Duration in minutes
    release_date TEXT,                    -- ISO date of release
    description TEXT,                     -- Description/summary
    cover_url TEXT,                       -- Cover image URL

    -- Classification
    content_type TEXT,                    -- Audible content_type (Podcast, Show, etc.)
    category TEXT NOT NULL DEFAULT 'podcast',  -- Mapped category: podcast, news, meditation, documentary, show, other

    -- Status tracking
    is_downloaded INTEGER DEFAULT 0,      -- 0=not downloaded, 1=in main library
    download_requested INTEGER DEFAULT 0, -- 0=no, 1=queued for download
    download_priority INTEGER DEFAULT 0,  -- Higher = sooner (for queue ordering)

    -- Sync metadata
    last_synced TEXT,                     -- ISO timestamp of last API sync

    -- Timestamps
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_periodicals_asin ON periodicals(asin);
CREATE INDEX IF NOT EXISTS idx_periodicals_category ON periodicals(category);
CREATE INDEX IF NOT EXISTS idx_periodicals_content_type ON periodicals(content_type);
CREATE INDEX IF NOT EXISTS idx_periodicals_downloaded ON periodicals(is_downloaded);
CREATE INDEX IF NOT EXISTS idx_periodicals_queued ON periodicals(download_requested) WHERE download_requested = 1;
CREATE INDEX IF NOT EXISTS idx_periodicals_release ON periodicals(release_date DESC);

-- Trigger to update updated_at on modification
DROP TRIGGER IF EXISTS periodicals_updated_at;
CREATE TRIGGER periodicals_updated_at
AFTER UPDATE ON periodicals
FOR EACH ROW
BEGIN
    UPDATE periodicals SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- Sync status table for SSE progress tracking
CREATE TABLE IF NOT EXISTS periodicals_sync_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_id TEXT NOT NULL UNIQUE,         -- UUID for this sync run
    status TEXT DEFAULT 'pending',        -- 'pending', 'running', 'completed', 'failed'
    started_at TEXT,
    completed_at TEXT,
    total_parents INTEGER DEFAULT 0,      -- Total items to process
    processed_parents INTEGER DEFAULT 0,  -- Items processed so far
    total_episodes INTEGER DEFAULT 0,     -- Same as processed for new model
    new_episodes INTEGER DEFAULT 0,       -- New items found
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Download queue view for easy querying
DROP VIEW IF EXISTS periodicals_download_queue;
CREATE VIEW periodicals_download_queue AS
SELECT
    p.id,
    p.asin,
    p.title,
    p.category,
    p.content_type,
    p.download_priority,
    p.created_at as queued_at
FROM periodicals p
WHERE p.download_requested = 1 AND p.is_downloaded = 0
ORDER BY p.download_priority DESC, p.created_at ASC;

-- Summary view by category
DROP VIEW IF EXISTS periodicals_summary;
CREATE VIEW periodicals_summary AS
SELECT
    category,
    COUNT(*) as total_items,
    SUM(CASE WHEN is_downloaded = 1 THEN 1 ELSE 0 END) as downloaded_count,
    SUM(CASE WHEN download_requested = 1 AND is_downloaded = 0 THEN 1 ELSE 0 END) as queued_count,
    MAX(last_synced) as last_synced
FROM periodicals
GROUP BY category;
