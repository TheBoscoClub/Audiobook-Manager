-- Migration 015: User suggestions / comment pad
-- Stores user feedback submitted from the Help page

CREATE TABLE IF NOT EXISTS user_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_suggestions_read ON user_suggestions(is_read);
CREATE INDEX IF NOT EXISTS idx_suggestions_created ON user_suggestions(created_at);
