-- Migration: Add playback position tracking columns to periodicals
-- Version: 3.9.0
-- Date: 2026-01-14
--
-- Adds columns to track playback position from both local player
-- and Audible cloud sync (Whispersync) for periodical content.

-- Add position tracking columns to periodicals table
ALTER TABLE periodicals ADD COLUMN playback_position_ms INTEGER DEFAULT 0;
ALTER TABLE periodicals ADD COLUMN playback_position_updated TEXT;
ALTER TABLE periodicals ADD COLUMN audible_position_ms INTEGER;
ALTER TABLE periodicals ADD COLUMN audible_position_updated TEXT;
ALTER TABLE periodicals ADD COLUMN position_synced_at TEXT;

-- Create index for quick position queries
CREATE INDEX IF NOT EXISTS idx_periodicals_position ON periodicals(playback_position_ms);
CREATE INDEX IF NOT EXISTS idx_periodicals_asin_position ON periodicals(asin, playback_position_ms);

-- Create position history table for periodicals
CREATE TABLE IF NOT EXISTS periodicals_playback_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    periodical_asin TEXT NOT NULL,  -- Using ASIN since periodicals use ASIN as primary identifier
    position_ms INTEGER NOT NULL,
    source TEXT NOT NULL,  -- 'local', 'audible', 'sync'
    recorded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (periodical_asin) REFERENCES periodicals(asin) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_periodicals_history_asin ON periodicals_playback_history(periodical_asin);
CREATE INDEX IF NOT EXISTS idx_periodicals_history_recorded ON periodicals_playback_history(recorded_at);

-- View for periodicals with Audible sync capability
CREATE VIEW IF NOT EXISTS periodicals_syncable AS
SELECT
    id,
    title,
    author,
    asin,
    runtime_minutes,
    playback_position_ms,
    playback_position_updated,
    audible_position_ms,
    audible_position_updated,
    position_synced_at,
    CASE
        WHEN runtime_minutes > 0 THEN
            ROUND(CAST(playback_position_ms AS REAL) / (runtime_minutes * 60000) * 100, 1)
        ELSE 0
    END as percent_complete
FROM periodicals
WHERE asin IS NOT NULL AND asin != '';
