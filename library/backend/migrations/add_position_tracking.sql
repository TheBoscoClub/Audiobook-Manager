-- Migration: Add playback position tracking columns
-- Version: 3.8.0
-- Date: 2026-01-07
--
-- Adds columns to track playback position from both local player
-- and Audible cloud sync for bidirectional synchronization.

-- Add position tracking columns to audiobooks table
ALTER TABLE audiobooks ADD COLUMN playback_position_ms INTEGER DEFAULT 0;
ALTER TABLE audiobooks ADD COLUMN playback_position_updated TIMESTAMP;
ALTER TABLE audiobooks ADD COLUMN audible_position_ms INTEGER;
ALTER TABLE audiobooks ADD COLUMN audible_position_updated TIMESTAMP;
ALTER TABLE audiobooks ADD COLUMN position_synced_at TIMESTAMP;

-- Create index for quick position queries
CREATE INDEX IF NOT EXISTS idx_audiobooks_position ON audiobooks(playback_position_ms);
CREATE INDEX IF NOT EXISTS idx_audiobooks_asin_position ON audiobooks(asin, playback_position_ms);

-- Create position history table for detailed tracking (optional feature)
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
