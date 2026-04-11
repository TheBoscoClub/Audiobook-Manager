-- Migration 016: Audiobook translations table for localized metadata
-- Stores per-locale translations of book card fields (title, author display, description)

CREATE TABLE IF NOT EXISTS audiobook_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,               -- BCP 47 tag: 'zh-Hans', 'ja', 'ko', etc.
    title TEXT,                         -- Translated title
    author_display TEXT,                -- Translated/transliterated author name
    series_display TEXT,                -- Translated series name
    description TEXT,                   -- Translated description/summary
    translator TEXT,                    -- Who/what translated ('deepl', 'manual', etc.)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(audiobook_id, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audiobook_translations_locale ON audiobook_translations(locale);
CREATE INDEX IF NOT EXISTS idx_audiobook_translations_book ON audiobook_translations(audiobook_id);
