-- Migration: 008_periodicals_parent_asin.sql
-- Description: Add parent_asin column to track parent/episode relationships
-- Date: 2026-01-14
--
-- This enables storing podcast episodes alongside their parent subscriptions.
-- Episodes have parent_asin set; parent items have parent_asin NULL.
--
-- content_delivery_type differentiates:
--   - NULL or empty: parent item
--   - "PodcastEpisode": individual podcast episode

-- Add parent_asin column for episode tracking
ALTER TABLE periodicals ADD COLUMN parent_asin TEXT DEFAULT NULL;

-- Add content_delivery_type column to distinguish parents from episodes
ALTER TABLE periodicals ADD COLUMN content_delivery_type TEXT DEFAULT NULL;

-- Index for efficient parent->episodes lookup
CREATE INDEX IF NOT EXISTS idx_periodicals_parent_asin ON periodicals(parent_asin);

-- Index for filtering by content_delivery_type
CREATE INDEX IF NOT EXISTS idx_periodicals_delivery_type ON periodicals(content_delivery_type);

-- View for parents only (podcast series, shows, etc.)
DROP VIEW IF EXISTS periodicals_parents;
CREATE VIEW periodicals_parents AS
SELECT
    p.*,
    (SELECT COUNT(*) FROM periodicals e WHERE e.parent_asin = p.asin) as episode_count
FROM periodicals p
WHERE p.parent_asin IS NULL;

-- View for episodes with parent info
DROP VIEW IF EXISTS periodicals_episodes;
CREATE VIEW periodicals_episodes AS
SELECT
    e.*,
    parent.title as parent_title,
    parent.cover_url as parent_cover_url
FROM periodicals e
JOIN periodicals parent ON e.parent_asin = parent.asin
WHERE e.parent_asin IS NOT NULL;
