-- Migration: 010_drop_periodicals.sql
-- Remove periodicals tables after feature extraction
-- BREAKING CHANGE: Periodicals feature removed in v4.0.0
--
-- This migration should be run AFTER upgrading to v4.0.0 to clean up
-- the database schema. The periodicals code has been extracted to the
-- feature/periodicals-rnd branch for future R&D.
--
-- To restore periodicals functionality, use:
--   git checkout v3.11.2-with-periodicals
-- or
--   git checkout feature/periodicals-rnd

-- Drop views first (they depend on the tables)
DROP VIEW IF EXISTS periodicals_download_queue;
DROP VIEW IF EXISTS periodicals_summary;
DROP VIEW IF EXISTS periodicals_parents;
DROP VIEW IF EXISTS periodicals_episodes;
DROP VIEW IF EXISTS periodicals_syncable;

-- Drop triggers
DROP TRIGGER IF EXISTS periodicals_updated_at;

-- Drop tables
DROP TABLE IF EXISTS periodicals_playback_history;
DROP TABLE IF EXISTS periodicals_sync_status;
DROP TABLE IF EXISTS periodicals;

-- Note: The content_type column in audiobooks table is KEPT
-- It's still useful for filtering and has no dependency on periodicals
