-- Migration 009: Multi-session login support
-- Adds system_settings table for global admin settings
-- and multi_session column to users for per-user override.

CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);

INSERT OR IGNORE INTO system_settings (setting_key, setting_value)
VALUES ('multi_session_default', 'false');

INSERT OR IGNORE INTO schema_version (version) VALUES (9);
