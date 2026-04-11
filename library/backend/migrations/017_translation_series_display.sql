-- Migration 017: Add series_display column to audiobook_translations
-- Stores per-locale translation of the book series name so cards
-- can overlay the translated series alongside title and author.
--
-- Safe to re-run: SQLite does not support ADD COLUMN IF NOT EXISTS,
-- so the backend performs this ALTER at startup wrapped in a try/except
-- that ignores "duplicate column name" errors.

ALTER TABLE audiobook_translations ADD COLUMN series_display TEXT;
