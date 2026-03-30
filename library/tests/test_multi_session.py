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


class TestUserMultiSessionField:
    def test_user_has_multi_session_default(self, temp_db):
        user = User(
            username="ms_test1",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)
        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,))
            fetched = User.from_row(cursor.fetchone())
        assert fetched.multi_session == "default"

    def test_user_multi_session_save_and_load(self, temp_db):
        user = User(
            username="ms_test2",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            multi_session="yes",
        )
        user.save(temp_db)
        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,))
            fetched = User.from_row(cursor.fetchone())
        assert fetched.multi_session == "yes"

    def test_user_multi_session_update(self, temp_db):
        user = User(
            username="ms_test3",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)
        assert user.multi_session == "default"
        user.multi_session = "no"
        user.save(temp_db)
        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,))
            fetched = User.from_row(cursor.fetchone())
        assert fetched.multi_session == "no"


class TestSystemSettingsRepository:
    def test_get_existing_setting(self, temp_db):
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        assert repo.get("multi_session_default") == "false"

    def test_get_nonexistent_setting(self, temp_db):
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        assert repo.get("nonexistent_key") is None

    def test_get_with_default(self, temp_db):
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        assert repo.get("nonexistent_key", "fallback") == "fallback"

    def test_set_new_setting(self, temp_db):
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        repo.set("test_key", "test_value")
        assert repo.get("test_key") == "test_value"

    def test_set_overwrites_existing(self, temp_db):
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        repo.set("multi_session_default", "true")
        assert repo.get("multi_session_default") == "true"

    def test_get_all(self, temp_db):
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        repo.set("extra_key", "extra_val")
        all_settings = repo.get_all()
        assert all_settings["multi_session_default"] == "false"
        assert all_settings["extra_key"] == "extra_val"
