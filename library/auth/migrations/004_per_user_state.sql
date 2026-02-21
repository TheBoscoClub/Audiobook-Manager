-- Migration 004: Per-user state tables
-- Adds listening history, download tracking, and user preferences

CREATE TABLE IF NOT EXISTS user_listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    position_start_ms INTEGER NOT NULL DEFAULT 0,
    position_end_ms INTEGER,
    duration_listened_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ulh_user ON user_listening_history(user_id);
CREATE INDEX IF NOT EXISTS idx_ulh_audiobook ON user_listening_history(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_ulh_started ON user_listening_history(started_at);

CREATE TABLE IF NOT EXISTS user_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    downloaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_format TEXT
);
CREATE INDEX IF NOT EXISTS idx_ud_user ON user_downloads(user_id);
CREATE INDEX IF NOT EXISTS idx_ud_audiobook ON user_downloads(audiobook_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_books_seen_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version) VALUES (4);
