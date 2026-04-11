-- Migration 010: preferred locale for users and access requests
-- Adds an additive preferred_locale column (default 'en') to both the
-- users table and the access_requests table so guest-facing emails
-- (magic link, invitation, activation, approval, denial, reply) can be
-- delivered in the recipient's language. Existing rows default to 'en'.
--
-- SQLite has no "ADD COLUMN IF NOT EXISTS", so the application-side
-- migration runner must tolerate "duplicate column" errors for
-- re-runs. The AccessRequestRepository and UserRepository already use
-- PRAGMA table_info() checks — this file is the canonical schema
-- record for fresh installs via install.sh.

ALTER TABLE users ADD COLUMN preferred_locale TEXT DEFAULT 'en';
ALTER TABLE access_requests ADD COLUMN preferred_locale TEXT DEFAULT 'en';

INSERT OR IGNORE INTO schema_version (version) VALUES (10);
