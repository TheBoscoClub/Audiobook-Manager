-- Migration 020: DeepL quota + glossary state.
-- Single-row bookkeeping table that the translation layer uses to:
--   * track characters billed in the current month (soft/hard limits)
--   * cache the glossary ID returned by DeepL's /v2/glossaries endpoint
--     so we do not rebuild it on every process start
--   * remember when the monthly DeepL quota window last rolled over
--
-- The row is keyed by a fixed string 'default' so the module can always
-- upsert against the same record without race-prone auto-increment IDs.
--
-- char_limit defaults to 500000 (DeepL free tier). Paid tier users can
-- raise it via the admin endpoint once that surface is wired up.

CREATE TABLE IF NOT EXISTS deepl_quota (
    id TEXT PRIMARY KEY DEFAULT 'default',
    chars_used INTEGER NOT NULL DEFAULT 0,
    char_limit INTEGER NOT NULL DEFAULT 500000,
    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_api_check TIMESTAMP,
    glossary_id TEXT,
    glossary_source_hash TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO deepl_quota (id) VALUES ('default');
