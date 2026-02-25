-- Migration 007: Store audiobook title in activity tables
-- Denormalizes title into listening history and downloads so the admin
-- activity log can display human-readable names even when the library DB
-- is reimported with different autoincrement IDs.

ALTER TABLE user_listening_history ADD COLUMN title TEXT;
ALTER TABLE user_downloads ADD COLUMN title TEXT;

INSERT OR IGNORE INTO schema_version (version) VALUES (7);
