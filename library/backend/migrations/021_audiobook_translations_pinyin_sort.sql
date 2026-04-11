-- Migration 021: Add pinyin_sort column to audiobook_translations
--
-- Purpose: Enable pinyin-based ordering of Chinese (zh-Hans / zh-Hant)
-- translated titles. SQLite's default collation orders Chinese by UTF-8
-- codepoint, which has no relationship to Mandarin pronunciation or any
-- meaningful Chinese sort order. This column stores a lowercase,
-- tone-stripped pinyin representation of `title` that can be used as the
-- ORDER BY key when locale starts with 'zh'.
--
-- Populated by: library/backend/migrations/backfill_pinyin_sort.py
--               (also updated on write in api_modular/translations.py)
--
-- Idempotent guard: ALTER TABLE ... ADD COLUMN fails on re-run, so this
-- migration is wrapped by backfill_pinyin_sort.py which catches the
-- "duplicate column name" error before running the backfill.

ALTER TABLE audiobook_translations ADD COLUMN pinyin_sort TEXT;

CREATE INDEX IF NOT EXISTS idx_audiobook_translations_pinyin_sort
    ON audiobook_translations(locale, pinyin_sort);
