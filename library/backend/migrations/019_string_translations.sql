-- Migration 019: Generic string translation cache.
-- Stores per-locale translation of arbitrary UI strings (section
-- headings, tour titles, notification bodies, admin-authored
-- announcements) so the frontend can overlay translated text on
-- pages that do not have individual data-i18n catalog keys.
--
-- Keyed by (source_hash, locale) where source_hash is a short
-- SHA-256 of the source English string. Source stored verbatim so
-- cache misses can be repopulated without the caller sending
-- the original again.
--
-- translator column records "deepl", "manual", or "human" so
-- admin edits can be distinguished from machine translations.

CREATE TABLE IF NOT EXISTS string_translations (
    source_hash TEXT NOT NULL,
    locale TEXT NOT NULL,
    source TEXT NOT NULL,
    translation TEXT NOT NULL,
    translator TEXT DEFAULT 'deepl',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_hash, locale)
);

CREATE INDEX IF NOT EXISTS idx_string_translations_locale
    ON string_translations(locale);
