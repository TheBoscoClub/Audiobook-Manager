"""Tests for multi-session login feature."""

import os
import tempfile

import pytest

from auth.database import AuthDatabase
from auth.models import AuthType, Session, SessionRepository, User


@pytest.fixture
def temp_db():
    """Create a temporary encrypted database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test-auth.db")
        key_path = os.path.join(tmpdir, "test.key")
        db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
        db.initialize()
        yield db


class TestMultiSessionMigration:
    def test_system_settings_table_exists(self, temp_db):
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='system_settings'"
            )
            assert cursor.fetchone() is not None

    def test_multi_session_default_seeded(self, temp_db):
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT setting_value FROM system_settings"
                " WHERE setting_key = 'multi_session_default'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "false"

    def test_users_have_multi_session_column(self, temp_db):
        with temp_db.connection() as conn:
            conn.execute(
                "INSERT INTO users (username, auth_type, auth_credential)"
                " VALUES ('testuser', 'totp', X'00')"
            )
            cursor = conn.execute(
                "SELECT multi_session FROM users WHERE username = 'testuser'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "default"

    def test_schema_version_is_9(self, temp_db):
        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            assert cursor.fetchone()[0] >= 9
