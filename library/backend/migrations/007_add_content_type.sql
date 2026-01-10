-- Migration 007: Add content_type field to audiobooks table
-- Purpose: Distinguish audiobooks from periodicals to prevent content misplacement
-- Date: 2026-01-10

-- Add content_type field to audiobooks table
-- Default 'Product' = standard audiobook (Audible terminology)
-- Other types: 'Podcast', 'Newspaper / Magazine', 'Show', 'Radio/TV Program'
ALTER TABLE audiobooks ADD COLUMN content_type TEXT DEFAULT 'Product';

-- Add source_asin field to track original Audible ASIN for cross-referencing
-- This helps identify when an audiobook should be in periodicals instead
ALTER TABLE audiobooks ADD COLUMN source_asin TEXT;

-- Create index for content_type filtering (used in all library queries)
CREATE INDEX IF NOT EXISTS idx_audiobooks_content_type ON audiobooks(content_type);

-- View for main library that excludes periodical content types
CREATE VIEW IF NOT EXISTS library_audiobooks AS
SELECT * FROM audiobooks
WHERE content_type = 'Product' OR content_type IS NULL;

-- Note: After running this migration, you should:
-- 1. Run populate_asins.py to fetch content_type from Audible
-- 2. Or manually update content_type for existing entries based on periodicals table:
--    UPDATE audiobooks SET content_type = 'Periodical'
--    WHERE asin IN (SELECT asin FROM periodicals);
