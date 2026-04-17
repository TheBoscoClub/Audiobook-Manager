-- Audiobook Manager Auth Database Schema
-- Encrypted with SQLCipher (AES-256)
-- Version: 2.0.0

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    auth_type TEXT NOT NULL CHECK (auth_type IN ('passkey', 'fido2', 'totp', 'magic_link')),
    auth_credential BLOB NOT NULL,
    can_download BOOLEAN DEFAULT FALSE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    -- Recovery options (user's choice to store or not)
    recovery_email TEXT,           -- Optional, stored encrypted in SQLCipher
    recovery_phone TEXT,           -- Optional, stored encrypted in SQLCipher
    recovery_enabled BOOLEAN DEFAULT FALSE,
    last_audit_seen_id INTEGER DEFAULT 0,
    multi_session TEXT NOT NULL DEFAULT 'default',
    preferred_locale TEXT DEFAULT 'en',

    CHECK (length(username) >= 3 AND length(username) <= 24)
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of session token
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,  -- NULL = no expiry (until logout/kick)
    user_agent TEXT,
    ip_address TEXT,  -- For audit, not displayed to users
    is_persistent BOOLEAN DEFAULT 0  -- Persistent "remember me" sessions
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);

-- User positions table
CREATE TABLE IF NOT EXISTS user_positions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id INTEGER NOT NULL,  -- References audiobooks.db
    position_ms INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, audiobook_id)
);

CREATE INDEX IF NOT EXISTS idx_user_positions_user_id ON user_positions(user_id);

-- User listening history table
CREATE TABLE IF NOT EXISTS user_listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    title TEXT,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    position_start_ms INTEGER NOT NULL DEFAULT 0,
    position_end_ms INTEGER,
    duration_listened_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ulh_user ON user_listening_history(user_id);
CREATE INDEX IF NOT EXISTS idx_ulh_audiobook ON user_listening_history(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_ulh_started ON user_listening_history(started_at);

-- User downloads table
CREATE TABLE IF NOT EXISTS user_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    title TEXT,
    downloaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_format TEXT
);
CREATE INDEX IF NOT EXISTS idx_ud_user ON user_downloads(user_id);
CREATE INDEX IF NOT EXISTS idx_ud_audiobook ON user_downloads(audiobook_id);

-- User preferences table
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_books_seen_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- User hidden books table (soft-hide from My Library view)
CREATE TABLE IF NOT EXISTS user_hidden_books (
    user_id INTEGER NOT NULL,
    audiobook_id INTEGER NOT NULL,
    hidden_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, audiobook_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- User settings table (v8 key-value preferences)
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, setting_key)
);

CREATE INDEX IF NOT EXISTS idx_user_settings_user ON user_settings(user_id);

-- Pending registrations table
CREATE TABLE IF NOT EXISTS pending_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of verification token
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_token_hash ON pending_registrations(token_hash);

-- Notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('info', 'maintenance', 'outage', 'personal')),
    target_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL = all users
    starts_at TIMESTAMP,  -- NULL = immediately
    expires_at TIMESTAMP,  -- NULL = no expiry
    dismissable BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT DEFAULT 'admin'
);

CREATE INDEX IF NOT EXISTS idx_notifications_target ON notifications(target_user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_active ON notifications(starts_at, expires_at);

-- Notification dismissals table
CREATE TABLE IF NOT EXISTS notification_dismissals (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (notification_id, user_id)
);

-- Inbox table (user messages to admin)
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    reply_via TEXT NOT NULL CHECK (reply_via IN ('in-app', 'email')),
    reply_email TEXT,  -- Only if reply_via='email', deleted after reply
    status TEXT DEFAULT 'unread' CHECK (status IN ('unread', 'read', 'replied', 'archived')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    replied_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);

-- Contact log (audit trail, no content)
CREATE TABLE IF NOT EXISTS contact_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Backup codes table (for users who choose not to store recovery contact)
CREATE TABLE IF NOT EXISTS backup_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,      -- SHA-256 of the backup code
    used_at TIMESTAMP,            -- NULL if unused, timestamp when used
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_backup_codes_user_id ON backup_codes(user_id);
CREATE INDEX IF NOT EXISTS idx_backup_codes_hash ON backup_codes(code_hash);

-- Pending recovery requests (for magic link recovery)
CREATE TABLE IF NOT EXISTS pending_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of recovery token
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP                 -- NULL if unused
);

CREATE INDEX IF NOT EXISTS idx_pending_recovery_token ON pending_recovery(token_hash);
CREATE INDEX IF NOT EXISTS idx_pending_recovery_user ON pending_recovery(user_id);

-- Access requests table (pending admin approval)
-- Column order MUST match AccessRequestRepository._ensure_table() and
-- AccessRequestRepository._AR_SELECT in library/auth/models.py. Any
-- new column must be appended at the END both here and in _ensure_table.
CREATE TABLE IF NOT EXISTS access_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    reviewed_at TIMESTAMP,
    reviewed_by TEXT,  -- Admin username who reviewed
    deny_reason TEXT,  -- Optional reason for denial
    claim_token_hash TEXT,  -- Hashed claim token for credential retrieval
    contact_email TEXT,  -- Optional contact email provided by the user
    totp_secret TEXT,  -- Base32-encoded TOTP secret (post-approval)
    totp_uri TEXT,  -- otpauth:// URI
    backup_codes_json TEXT,  -- JSON array of TOTP backup codes
    credentials_claimed BOOLEAN DEFAULT FALSE,  -- set once the claim is consumed
    preferred_auth_method TEXT DEFAULT 'totp',  -- totp, passkey, magic_link
    claim_expires_at TIMESTAMP,  -- Expiry for invitation claim tokens
    preferred_locale TEXT DEFAULT 'en',  -- Locale for guest-facing emails

    CHECK (length(username) >= 3 AND length(username) <= 24)
);

CREATE INDEX IF NOT EXISTS idx_access_requests_status ON access_requests(status);
CREATE INDEX IF NOT EXISTS idx_access_requests_username ON access_requests(username);

-- WebAuthn credential details (for passkey/fido2 auth types)
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL,           -- Base64URL-encoded credential ID
    public_key TEXT NOT NULL,              -- Base64URL-encoded public key
    sign_count INTEGER DEFAULT 0,          -- Signature counter for replay detection
    transports TEXT,                       -- Comma-separated transport hints (usb, nfc, ble, internal)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(user_id, credential_id)
);

CREATE INDEX IF NOT EXISTS idx_webauthn_user_id ON webauthn_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_webauthn_cred_id ON webauthn_credentials(credential_id);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version) VALUES (10);

-- System settings table (global admin key-value store)
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);

INSERT OR IGNORE INTO system_settings (setting_key, setting_value)
VALUES ('multi_session_default', 'false');

-- Audit log for user management actions (nullable FKs survive user deletion)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    target_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
