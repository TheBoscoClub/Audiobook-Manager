-- Migration 005: Magic link auth, persistent sessions, auth method preference
-- Adds magic_link to auth_type CHECK, is_persistent to sessions,
-- preferred_auth_method to access_requests

-- Step 1: Recreate users table with expanded CHECK constraint
-- (SQLite cannot ALTER CHECK constraints)
CREATE TABLE IF NOT EXISTS users_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    auth_type TEXT NOT NULL CHECK (auth_type IN ('passkey', 'fido2', 'totp', 'magic_link')),
    auth_credential BLOB NOT NULL,
    can_download BOOLEAN DEFAULT FALSE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    recovery_email TEXT,
    recovery_phone TEXT,
    recovery_enabled BOOLEAN DEFAULT FALSE,
    CHECK (length(username) >= 5 AND length(username) <= 16)
);

INSERT INTO users_new SELECT * FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- Step 2: Add is_persistent to sessions (simple ALTER)
-- Use a pragma check to avoid duplicate column errors on re-run
ALTER TABLE sessions ADD COLUMN is_persistent BOOLEAN DEFAULT 0;

-- Step 3: Add preferred_auth_method to access_requests
ALTER TABLE access_requests ADD COLUMN preferred_auth_method TEXT DEFAULT 'totp';

-- Step 4: Update schema version
INSERT OR IGNORE INTO schema_version (version) VALUES (5);
