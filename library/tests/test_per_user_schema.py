# library/tests/test_per_user_schema.py
"""Tests for per-user state schema additions."""

import sqlite3
import os
from pathlib import Path
import pytest


def get_schema_sql():
    """Read the full schema.sql file."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "auth", "schema.sql")
    with open(schema_path) as f:
        return f.read()


def get_migration_sql():
    """Read migration 004."""
    migration_path = os.path.join(
        os.path.dirname(__file__), "..", "auth", "migrations", "004_per_user_state.sql"
    )
    with open(migration_path) as f:
        return f.read()


class TestPerUserStateTables:
    """Verify new tables exist and have correct structure."""

    @pytest.fixture
    def db(self):
        """Create in-memory DB with full schema."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(get_schema_sql())
        conn.commit()
        yield conn
        conn.close()

    def test_user_listening_history_table_exists(self, db):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_listening_history'"
        )
        assert cursor.fetchone() is not None

    def test_user_listening_history_columns(self, db):
        cursor = db.execute("PRAGMA table_info(user_listening_history)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "user_id" in columns
        assert "audiobook_id" in columns
        assert "started_at" in columns
        assert "ended_at" in columns
        assert "position_start_ms" in columns
        assert "position_end_ms" in columns
        assert "duration_listened_ms" in columns

    def test_user_downloads_table_exists(self, db):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_downloads'"
        )
        assert cursor.fetchone() is not None

    def test_user_downloads_columns(self, db):
        cursor = db.execute("PRAGMA table_info(user_downloads)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "user_id" in columns
        assert "audiobook_id" in columns
        assert "downloaded_at" in columns
        assert "file_format" in columns

    def test_user_preferences_table_exists(self, db):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'"
        )
        assert cursor.fetchone() is not None

    def test_user_preferences_columns(self, db):
        cursor = db.execute("PRAGMA table_info(user_preferences)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "user_id" in columns
        assert "new_books_seen_at" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_cascade_delete_listening_history(self, db):
        """Deleting a user cascades to listening history."""
        db.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        db.execute(
            "INSERT INTO user_listening_history (user_id, audiobook_id, position_start_ms) VALUES (1, 100, 0)"
        )
        db.commit()
        db.execute("DELETE FROM users WHERE id = 1")
        db.commit()
        cursor = db.execute(
            "SELECT COUNT(*) FROM user_listening_history WHERE user_id = 1"
        )
        assert cursor.fetchone()[0] == 0

    def test_cascade_delete_downloads(self, db):
        """Deleting a user cascades to downloads."""
        db.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        db.execute("INSERT INTO user_downloads (user_id, audiobook_id) VALUES (1, 100)")
        db.commit()
        db.execute("DELETE FROM users WHERE id = 1")
        db.commit()
        cursor = db.execute("SELECT COUNT(*) FROM user_downloads WHERE user_id = 1")
        assert cursor.fetchone()[0] == 0

    def test_cascade_delete_preferences(self, db):
        """Deleting a user cascades to preferences."""
        db.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        db.execute("INSERT INTO user_preferences (user_id) VALUES (1)")
        db.commit()
        db.execute("DELETE FROM users WHERE id = 1")
        db.commit()
        cursor = db.execute("SELECT COUNT(*) FROM user_preferences WHERE user_id = 1")
        assert cursor.fetchone()[0] == 0

    def test_schema_version_updated(self, db):
        cursor = db.execute("SELECT MAX(version) FROM schema_version")
        version = cursor.fetchone()[0]
        assert version >= 4

    def test_indexes_exist(self, db):
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_ulh_user" in indexes
        assert "idx_ulh_audiobook" in indexes
        assert "idx_ulh_started" in indexes
        assert "idx_ud_user" in indexes
        assert "idx_ud_audiobook" in indexes


class TestMigration004:
    """Test migration applies cleanly to existing v3 schema."""

    @pytest.fixture
    def db_v3(self):
        """Create DB with full schema (v4 — tests migration idempotency)."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(get_schema_sql())  # Schema is v4; tests idempotency
        conn.commit()
        yield conn
        conn.close()

    def test_migration_is_idempotent(self, db_v3):
        """Running migration twice does not error (IF NOT EXISTS)."""
        migration = get_migration_sql()
        db_v3.executescript(migration)
        db_v3.executescript(migration)  # Second run should not fail


class TestMigrationRunner:
    """Test _apply_migrations() in AuthDatabase upgrades a v3 database to v4."""

    MIGRATIONS_DIR = Path(__file__).parent.parent / "auth" / "migrations"

    def _build_v3_schema(self, conn: sqlite3.Connection) -> None:
        """
        Populate conn with the v3 schema: all original tables, version=3,
        without the three new v4 tables.  This simulates a production DB that
        pre-dates migration 004.
        """
        # Rebuild v3 schema explicitly (not derived from schema.sql) so this test
        # remains valid even as schema.sql evolves past v4.
        v3_tables_sql = """
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
            CREATE TABLE IF NOT EXISTS user_positions (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                audiobook_id INTEGER NOT NULL,
                position_ms INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, audiobook_id)
            );
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO schema_version (version) VALUES (3);
        """
        conn.executescript(v3_tables_sql)
        conn.commit()

    def _run_migration_runner(self, conn: sqlite3.Connection) -> None:
        """
        Reproduce the _apply_migrations() logic from AuthDatabase using a
        plain sqlite3 connection (no SQLCipher dependency in unit tests).
        """
        migrations_dir = self.MIGRATIONS_DIR
        if not migrations_dir.exists():
            return
        cursor = conn.execute("SELECT MAX(version) FROM schema_version")
        current_version = cursor.fetchone()[0] or 0
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            version = int(migration_file.stem.split("_")[0])
            if version > current_version:
                migration_sql = migration_file.read_text()
                conn.executescript(migration_sql)
                current_version = version

    def test_migration_runner_upgrades_v3_to_v4(self):
        """Migration runner applies 004 to a v3 DB, creating all new tables."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._build_v3_schema(conn)

            # Confirm we're at v3 before running
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            assert cursor.fetchone()[0] == 3

            self._run_migration_runner(conn)

            # Version bumped to 4
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            assert cursor.fetchone()[0] == 4

            # All three new tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cursor.fetchall()}
            assert "user_listening_history" in tables
            assert "user_downloads" in tables
            assert "user_preferences" in tables
        finally:
            conn.close()

    def test_migration_runner_skips_already_applied(self):
        """Migration runner does not re-apply migrations already at current version."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # Start at full v4 schema
            conn.executescript(get_schema_sql())
            conn.commit()

            # Drop one table to detect if migration re-ran
            conn.execute("DROP TABLE user_downloads")
            conn.commit()

            # Runner should see version=4 >= 4 and skip migration 004
            self._run_migration_runner(conn)

            # Table should still be absent (migration was skipped)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_downloads'"
            )
            assert cursor.fetchone() is None
        finally:
            conn.close()
