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


class TestSessionAllowMulti:
    def _make_user(self, temp_db, username="session_user"):
        user = User(
            username=username,
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)
        return user

    def test_default_behavior_single_session(self, temp_db):
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)
        session1, token1 = Session.create_for_user(temp_db, user.id)
        session2, token2 = Session.create_for_user(temp_db, user.id)
        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is not None

    def test_allow_multi_preserves_sessions(self, temp_db):
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)
        session1, token1 = Session.create_for_user(temp_db, user.id)
        session2, token2 = Session.create_for_user(
            temp_db, user.id, allow_multi=True
        )
        assert repo.get_by_token(token1) is not None
        assert repo.get_by_token(token2) is not None

    def test_allow_multi_false_still_deletes(self, temp_db):
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)
        session1, token1 = Session.create_for_user(temp_db, user.id)
        session2, token2 = Session.create_for_user(
            temp_db, user.id, allow_multi=False
        )
        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is not None

    def test_allow_multi_three_sessions(self, temp_db):
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)
        _, token1 = Session.create_for_user(temp_db, user.id)
        _, token2 = Session.create_for_user(
            temp_db, user.id, allow_multi=True
        )
        _, token3 = Session.create_for_user(
            temp_db, user.id, allow_multi=True
        )
        assert repo.get_by_token(token1) is not None
        assert repo.get_by_token(token2) is not None
        assert repo.get_by_token(token3) is not None

    def test_single_session_after_multi_clears_all(self, temp_db):
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)
        _, token1 = Session.create_for_user(temp_db, user.id)
        _, token2 = Session.create_for_user(
            temp_db, user.id, allow_multi=True
        )
        _, token3 = Session.create_for_user(
            temp_db, user.id, allow_multi=False
        )
        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is None
        assert repo.get_by_token(token3) is not None


class TestUserAllowsMultiSession:
    """Tests for _user_allows_multi_session() resolution logic."""

    def _make_user(self, temp_db, username, multi_session="default"):
        user = User(
            username=username,
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            multi_session=multi_session,
        )
        user.save(temp_db)
        return user

    def test_user_yes_overrides_global_false(self, temp_db):
        from auth.models import SystemSettingsRepository
        SystemSettingsRepository(temp_db).set("multi_session_default", "false")
        user = self._make_user(temp_db, "override_yes", multi_session="yes")
        from backend.api_modular.auth import _user_allows_multi_session
        assert _user_allows_multi_session(user, temp_db) is True

    def test_user_no_overrides_global_true(self, temp_db):
        from auth.models import SystemSettingsRepository
        SystemSettingsRepository(temp_db).set("multi_session_default", "true")
        user = self._make_user(temp_db, "override_no", multi_session="no")
        from backend.api_modular.auth import _user_allows_multi_session
        assert _user_allows_multi_session(user, temp_db) is False

    def test_user_default_follows_global_false(self, temp_db):
        from auth.models import SystemSettingsRepository
        SystemSettingsRepository(temp_db).set("multi_session_default", "false")
        user = self._make_user(temp_db, "follow_false")
        from backend.api_modular.auth import _user_allows_multi_session
        assert _user_allows_multi_session(user, temp_db) is False

    def test_user_default_follows_global_true(self, temp_db):
        from auth.models import SystemSettingsRepository
        SystemSettingsRepository(temp_db).set("multi_session_default", "true")
        user = self._make_user(temp_db, "follow_true")
        from backend.api_modular.auth import _user_allows_multi_session
        assert _user_allows_multi_session(user, temp_db) is True


class TestAdminSettingsAPI:
    """Tests for GET/PATCH /auth/admin/settings endpoints."""

    @pytest.fixture
    def app_client(self, temp_db, monkeypatch):
        import sys
        lib_dir = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
        if lib_dir not in sys.path:
            sys.path.insert(0, os.path.abspath(lib_dir))

        from backend.api_modular.auth import auth_bp, get_auth_db
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(auth_bp)

        monkeypatch.setattr("backend.api_modular.auth.get_auth_db", lambda: temp_db)

        admin = User(
            username="admin_settings",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            is_admin=True,
        )
        admin.save(temp_db)
        session, token = Session.create_for_user(temp_db, admin.id)

        client = app.test_client()
        client.set_cookie("audiobooks_session", token, domain="localhost")
        return client

    def test_get_settings(self, app_client):
        resp = app_client.get("/auth/admin/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "multi_session_default" in data
        assert data["multi_session_default"] == "false"

    def test_patch_settings(self, app_client):
        resp = app_client.patch(
            "/auth/admin/settings",
            json={"multi_session_default": "true"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        resp = app_client.get("/auth/admin/settings")
        data = resp.get_json()
        assert data["multi_session_default"] == "true"

    def test_patch_settings_rejects_empty(self, app_client):
        resp = app_client.patch("/auth/admin/settings", json={})
        assert resp.status_code == 400
