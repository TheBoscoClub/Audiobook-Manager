-- Migration 006: Add webauthn_credentials table
-- Stores WebAuthn credential details for passkey/fido2 users
-- Required for auth method switching (TOTP -> passkey) in Edit Profile

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

INSERT OR IGNORE INTO schema_version (version) VALUES (6);
