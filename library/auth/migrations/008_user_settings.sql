-- Migration 008: Key-value user settings table for v8 preferences
-- Stores browsing, playback, and accessibility preferences per user.
-- All values are TEXT (JSON-safe strings). Defaults are enforced in application code.

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, setting_key)
);

CREATE INDEX IF NOT EXISTS idx_user_settings_user ON user_settings(user_id);

INSERT OR IGNORE INTO schema_version (version) VALUES (8);
