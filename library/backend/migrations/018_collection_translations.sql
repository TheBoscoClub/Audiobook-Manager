-- Migration 018: Collection translation cache.
-- Stores per-locale translation of collection display names (genres,
-- subgenres, series, eras, topics) so the sidebar can overlay translated
-- labels without re-calling DeepL on every page load.
--
-- Keyed by (collection_id, locale). collection_id is the stable slug
-- used by the /api/collections endpoint (e.g., "fiction-mystery",
-- "topics-17th-century", "special-lectures").
--
-- translator column records the source ("deepl", "manual", "human")
-- so admin edits can be distinguished from machine translations.

CREATE TABLE IF NOT EXISTS collection_translations (
    collection_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    name TEXT NOT NULL,
    translator TEXT DEFAULT 'deepl',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id, locale)
);

CREATE INDEX IF NOT EXISTS idx_collection_translations_locale
    ON collection_translations(locale);
