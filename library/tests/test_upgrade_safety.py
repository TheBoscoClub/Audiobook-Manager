"""
Tests for upgrade safety — verifying that auth data survives schema migrations.

These tests create a v4 database with users, sessions, pending access_requests
with claim tokens, then run the v5 migration, and verify all data is intact.
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

try:
    import sqlcipher3 as sqlcipher
except ImportError:
    sqlcipher = None

pytestmark = pytest.mark.skipif(sqlcipher is None, reason="sqlcipher3 not installed")


# V4 schema SQL — no magic_link, no is_persistent, no preferred_auth_method
V4_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    auth_type TEXT NOT NULL CHECK (auth_type IN ('passkey', 'fido2', 'totp')),
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
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    user_agent TEXT,
    ip_address TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);

CREATE TABLE IF NOT EXISTS pending_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_token_hash ON pending_registrations(token_hash);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('info', 'maintenance', 'outage', 'personal')),
    target_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    starts_at TIMESTAMP,
    expires_at TIMESTAMP,
    dismissable BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT DEFAULT 'admin'
);
CREATE INDEX IF NOT EXISTS idx_notifications_target ON notifications(target_user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_active ON notifications(starts_at, expires_at);

CREATE TABLE IF NOT EXISTS notification_dismissals (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notification_id, user_id)
);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    reply_via TEXT NOT NULL CHECK (reply_via IN ('in-app', 'email')),
    reply_email TEXT,
    status TEXT DEFAULT 'unread' CHECK (status IN ('unread', 'read', 'replied', 'archived')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    replied_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);

CREATE TABLE IF NOT EXISTS contact_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backup_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_backup_codes_user_id ON backup_codes(user_id);
CREATE INDEX IF NOT EXISTS idx_backup_codes_hash ON backup_codes(code_hash);

CREATE TABLE IF NOT EXISTS pending_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pending_recovery_token ON pending_recovery(token_hash);
CREATE INDEX IF NOT EXISTS idx_pending_recovery_user ON pending_recovery(user_id);

CREATE TABLE IF NOT EXISTS access_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    reviewed_at TIMESTAMP,
    reviewed_by TEXT,
    deny_reason TEXT,
    CHECK (length(username) >= 5 AND length(username) <= 16)
);
CREATE INDEX IF NOT EXISTS idx_access_requests_status ON access_requests(status);
CREATE INDEX IF NOT EXISTS idx_access_requests_username ON access_requests(username);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO schema_version (version) VALUES (4);
"""


@pytest.fixture
def v4_db_path():
    """Create a temporary path for a v4 auth database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Remove so AuthDatabase sees it as new
    yield Path(path)
    if os.path.exists(path):
        os.unlink(path)
    # Clean up backup files
    backup = path + ".pre-v5-backup"
    if os.path.exists(backup):
        os.unlink(backup)


@pytest.fixture
def key_path():
    """Create a temporary encryption key (64 hex chars)."""
    fd, path = tempfile.mkstemp(suffix=".key")
    os.close(fd)
    with open(path, "w") as f:
        f.write("a" * 64)
    yield Path(path)
    os.unlink(path)


def create_v4_database(db_path, key_path):
    """Create a v4 schema database with test data using raw sqlcipher."""
    key = key_path.read_text().strip()

    conn = sqlcipher.connect(str(db_path))
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(V4_SCHEMA)

    # Insert test users
    conn.execute(
        """
        INSERT INTO users (username, auth_type, auth_credential, can_download, is_admin,
                           recovery_email, recovery_phone, recovery_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "testuser1",
            "totp",
            b"\x00\x01\x02",
            True,
            False,
            "test@example.com",
            None,
            True,
        ),
    )
    conn.execute(
        """
        INSERT INTO users (username, auth_type, auth_credential, can_download, is_admin,
                           recovery_email, recovery_phone, recovery_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("adminuser", "totp", b"\x03\x04\x05", True, True, None, None, False),
    )

    # Insert test sessions (v4: no is_persistent column)
    conn.execute(
        """
        INSERT INTO sessions (user_id, token_hash, user_agent, ip_address)
        VALUES (?, ?, ?, ?)
        """,
        (1, "hash_for_user1", "TestBrowser/1.0", "192.168.1.1"),
    )
    conn.execute(
        """
        INSERT INTO sessions (user_id, token_hash, user_agent, ip_address)
        VALUES (?, ?, ?, ?)
        """,
        (2, "hash_for_admin", "AdminBrowser/1.0", "192.168.1.2"),
    )

    # Insert pending recovery tokens
    expires = (datetime.now() + timedelta(minutes=15)).isoformat()
    conn.execute(
        """
        INSERT INTO pending_recovery (user_id, token_hash, expires_at)
        VALUES (?, ?, ?)
        """,
        (1, "recovery_token_hash", expires),
    )

    # Insert an access request (v4: no preferred_auth_method column)
    conn.execute(
        """
        INSERT INTO access_requests (username, status)
        VALUES (?, ?)
        """,
        ("pendinguser1", "pending"),
    )

    conn.commit()
    conn.close()


class TestUpgradeSafety:
    """Test that v4->v5 migration preserves all existing data."""

    def test_users_survive_migration(self, v4_db_path, key_path):
        """All users must exist after migration with identical data."""
        from library.auth.database import AuthDatabase
        from library.auth.models import UserRepository

        create_v4_database(v4_db_path, key_path)

        # Run migration via AuthDatabase.initialize()
        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        # Verify post-migration
        user_repo = UserRepository(db)
        users = user_repo.list_all()
        assert len(users) == 2

        # Verify specific user data survived
        user1 = user_repo.get_by_username("testuser1")
        assert user1 is not None
        assert user1.can_download is True
        assert user1.is_admin is False
        assert user1.recovery_email == "test@example.com"
        assert user1.recovery_enabled is True
        assert user1.auth_credential == b"\x00\x01\x02"

        admin = user_repo.get_by_username("adminuser")
        assert admin is not None
        assert admin.is_admin is True

    def test_sessions_survive_migration(self, v4_db_path, key_path):
        """All sessions must remain valid after migration."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase

        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        # Verify sessions exist and have is_persistent defaulted to 0
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT id, user_id, token_hash, is_persistent FROM sessions"
            ).fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row[3] == 0  # is_persistent defaults to False

    def test_recovery_tokens_survive_migration(self, v4_db_path, key_path):
        """Pending recovery tokens must still resolve after migration."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase

        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        with db.connection() as conn:
            rows = conn.execute("SELECT * FROM pending_recovery").fetchall()
            assert len(rows) == 1
            assert rows[0][2] == "recovery_token_hash"

    def test_access_requests_survive_migration(self, v4_db_path, key_path):
        """Access requests must survive and get preferred_auth_method default."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase

        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        with db.connection() as conn:
            rows = conn.execute(
                "SELECT username, status, preferred_auth_method FROM access_requests"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "pendinguser1"
            assert rows[0][1] == "pending"
            assert rows[0][2] == "totp"  # default added by migration

    def test_schema_version_updated(self, v4_db_path, key_path):
        """Schema version must be 5 after migration."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase

        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        with db.connection() as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            assert version == 5

    def test_magic_link_auth_type_works_after_migration(self, v4_db_path, key_path):
        """After migration, magic_link auth type must be insertable."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase
        from library.auth.models import User, AuthType

        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        # Create a magic_link user (would fail on v4 CHECK constraint)
        user = User(
            username="mluser",
            auth_type=AuthType.MAGIC_LINK,
            auth_credential=b"\x00",
            can_download=True,
            recovery_email="ml@example.com",
            recovery_enabled=True,
        )
        user.save(db)
        assert user.id is not None

    def test_migration_idempotent(self, v4_db_path, key_path):
        """Running migration twice must not corrupt data."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase
        from library.auth.models import UserRepository

        # Run migration once
        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        # Run migration again (should be a no-op)
        db2 = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db2.initialize()

        # Verify data intact
        user_repo = UserRepository(db2)
        users = user_repo.list_all()
        assert len(users) == 2

        with db2.connection() as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            assert version == 5

    def test_persistent_sessions_after_migration(self, v4_db_path, key_path):
        """New persistent sessions must work after migration."""
        create_v4_database(v4_db_path, key_path)

        from library.auth.database import AuthDatabase
        from library.auth.models import Session

        db = AuthDatabase(db_path=str(v4_db_path), key_path=str(key_path), is_dev=True)
        db.initialize()

        # Create a persistent session
        session, token = Session.create_for_user(db, user_id=1, remember_me=True)
        assert session.is_persistent is True

        # Verify the 30-day stale threshold
        assert session.is_stale() is False
