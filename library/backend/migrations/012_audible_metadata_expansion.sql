-- Migration 012: Audible Metadata Expansion
-- Adds columns and tables for comprehensive Audible catalog data
-- Safe: ALTER TABLE ADD COLUMN in SQLite doesn't rewrite the table

-- ================================================================
-- New columns on audiobooks table
-- ================================================================

-- Descriptive metadata
ALTER TABLE audiobooks ADD COLUMN subtitle TEXT;
ALTER TABLE audiobooks ADD COLUMN language TEXT;
ALTER TABLE audiobooks ADD COLUMN format_type TEXT;          -- Unabridged, Abridged, Original Recording
ALTER TABLE audiobooks ADD COLUMN runtime_length_min INTEGER; -- Audible's duration in minutes
ALTER TABLE audiobooks ADD COLUMN release_date TEXT;          -- Audible release date (may differ from published_date)
ALTER TABLE audiobooks ADD COLUMN publisher_summary TEXT;     -- Audible's HTML publisher summary

-- Ratings (from Audible)
ALTER TABLE audiobooks ADD COLUMN rating_overall REAL;
ALTER TABLE audiobooks ADD COLUMN rating_performance REAL;
ALTER TABLE audiobooks ADD COLUMN rating_story REAL;
ALTER TABLE audiobooks ADD COLUMN num_ratings INTEGER;
ALTER TABLE audiobooks ADD COLUMN num_reviews INTEGER;

-- Audible-specific identifiers and URLs
ALTER TABLE audiobooks ADD COLUMN audible_image_url TEXT;     -- Cover art URL from Audible
ALTER TABLE audiobooks ADD COLUMN sample_url TEXT;            -- Audio sample URL
ALTER TABLE audiobooks ADD COLUMN audible_sku TEXT;           -- Audible SKU identifier

-- Content flags
ALTER TABLE audiobooks ADD COLUMN is_adult_product INTEGER DEFAULT 0;  -- Boolean as integer
ALTER TABLE audiobooks ADD COLUMN merchandising_summary TEXT;

-- Enrichment tracking
ALTER TABLE audiobooks ADD COLUMN audible_enriched_at TIMESTAMP;  -- When metadata was last pulled from Audible
ALTER TABLE audiobooks ADD COLUMN isbn_enriched_at TIMESTAMP;     -- When metadata was last pulled from ISBN source

-- ================================================================
-- New table: Audible categories (hierarchical genre classification)
-- Audible's category_ladders provide hierarchical paths like:
--   "Science Fiction & Fantasy > Fantasy > Epic"
-- ================================================================

CREATE TABLE IF NOT EXISTS audible_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    category_path TEXT NOT NULL,          -- Full path: "Sci-Fi & Fantasy > Fantasy > Epic"
    category_name TEXT NOT NULL,          -- Leaf name: "Epic"
    root_category TEXT NOT NULL,          -- Root: "Science Fiction & Fantasy"
    depth INTEGER NOT NULL DEFAULT 1,     -- Depth in hierarchy (1=root, 2=child, etc.)
    audible_category_id TEXT,             -- Audible's internal category ID
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audible_categories_audiobook ON audible_categories(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_audible_categories_root ON audible_categories(root_category);
CREATE INDEX IF NOT EXISTS idx_audible_categories_name ON audible_categories(category_name);
CREATE INDEX IF NOT EXISTS idx_audible_categories_path ON audible_categories(category_path);

-- ================================================================
-- New table: Editorial reviews from Audible
-- ================================================================

CREATE TABLE IF NOT EXISTS editorial_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    review_text TEXT NOT NULL,
    source TEXT,                           -- e.g., "Publisher's Weekly", "AudioFile"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_editorial_reviews_audiobook ON editorial_reviews(audiobook_id);

-- ================================================================
-- Extend authors table with Audible ASIN for linking
-- ================================================================

ALTER TABLE authors ADD COLUMN asin TEXT;
CREATE INDEX IF NOT EXISTS idx_authors_asin ON authors(asin);

-- ================================================================
-- New indices for enriched data queries
-- ================================================================

CREATE INDEX IF NOT EXISTS idx_audiobooks_language ON audiobooks(language);
CREATE INDEX IF NOT EXISTS idx_audiobooks_format_type ON audiobooks(format_type);
CREATE INDEX IF NOT EXISTS idx_audiobooks_rating ON audiobooks(rating_overall);
CREATE INDEX IF NOT EXISTS idx_audiobooks_release_date ON audiobooks(release_date);
CREATE INDEX IF NOT EXISTS idx_audiobooks_audible_sku ON audiobooks(audible_sku);
CREATE INDEX IF NOT EXISTS idx_audiobooks_is_adult ON audiobooks(is_adult_product);
CREATE INDEX IF NOT EXISTS idx_audiobooks_enriched ON audiobooks(audible_enriched_at);
