"""
Tests for v8 User Settings (key-value preferences system).

Tests both the UserSettingsRepository (model layer) and the
/api/user/preferences endpoints (API layer).
"""

import json
import tempfile
from pathlib import Path

import pytest

from auth import AuthDatabase, AuthType, User, UserSettingsRepository
from auth.totp import generate_secret


def _create_test_user(db, username="testuser"):
    """Helper to create a test user via User.save()."""
    secret = generate_secret()
    user = User(username=username, auth_type=AuthType.TOTP, auth_credential=secret)
    user.save(db)
    return user


@pytest.fixture
def settings_db():
    """Create a temporary auth database with a test user."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test-auth.db"
        db = AuthDatabase(db_path=str(db_path), is_dev=True)
        db.initialize()

        user = _create_test_user(db)
        yield db, user


class TestUserSettingsRepository:
    """Test the key-value settings repository."""

    def test_get_all_defaults(self, settings_db):
        """Getting all settings for a new user returns defaults."""
        db, user = settings_db
        repo = UserSettingsRepository(db)
        settings = repo.get_all(user.id)

        assert settings["sort_order"] == "title_asc"
        assert settings["font_size"] == "16"
        assert settings["playback_speed"] == "1"
        assert settings["view_mode"] == "grid"
        assert len(settings) == len(UserSettingsRepository.DEFAULTS)

    def test_set_and_get(self, settings_db):
        """Setting a value persists and is returned by get."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        repo.set(user.id, "font_size", "20")
        assert repo.get(user.id, "font_size") == "20"

    def test_set_overrides_default(self, settings_db):
        """Setting a value overrides the default in get_all."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        repo.set(user.id, "playback_speed", "1.5")
        settings = repo.get_all(user.id)
        assert settings["playback_speed"] == "1.5"
        # Other defaults still intact
        assert settings["font_size"] == "16"

    def test_set_many(self, settings_db):
        """Setting multiple values at once works."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        count = repo.set_many(
            user.id, {"font_size": "18", "contrast": "high", "reduce_animations": "true"}
        )
        assert count == 3

        settings = repo.get_all(user.id)
        assert settings["font_size"] == "18"
        assert settings["contrast"] == "high"
        assert settings["reduce_animations"] == "true"

    def test_set_many_ignores_invalid_keys(self, settings_db):
        """set_many silently ignores keys not in VALID_KEYS."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        count = repo.set_many(
            user.id, {"font_size": "18", "invalid_key": "value", "another_bad": "key"}
        )
        assert count == 1

    def test_delete_resets_to_default(self, settings_db):
        """Deleting a setting makes get return the default."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        repo.set(user.id, "font_size", "20")
        assert repo.get(user.id, "font_size") == "20"

        repo.delete(user.id, "font_size")
        assert repo.get(user.id, "font_size") == "16"  # default

    def test_delete_all(self, settings_db):
        """Deleting all settings resets everything to defaults."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        repo.set_many(user.id, {"font_size": "20", "contrast": "high"})
        count = repo.delete_all(user.id)
        assert count == 2

        settings = repo.get_all(user.id)
        assert settings == UserSettingsRepository.DEFAULTS

    def test_invalid_key_raises(self, settings_db):
        """Accessing an invalid key raises ValueError."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        with pytest.raises(ValueError, match="Unknown setting key"):
            repo.get(user.id, "nonexistent_key")

        with pytest.raises(ValueError, match="Unknown setting key"):
            repo.set(user.id, "nonexistent_key", "value")

    def test_upsert_behavior(self, settings_db):
        """Setting the same key twice updates the value."""
        db, user = settings_db
        repo = UserSettingsRepository(db)

        repo.set(user.id, "font_size", "18")
        repo.set(user.id, "font_size", "20")
        assert repo.get(user.id, "font_size") == "20"

    def test_user_isolation(self, settings_db):
        """Settings are isolated between users."""
        db, user1 = settings_db
        repo = UserSettingsRepository(db)

        # Create a second user
        user2 = _create_test_user(db, username="testuser2")

        repo.set(user1.id, "font_size", "20")
        repo.set(user2.id, "font_size", "14")

        assert repo.get(user1.id, "font_size") == "20"
        assert repo.get(user2.id, "font_size") == "14"


class TestPreferencesAPI:
    """Test the /api/user/preferences endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a test Flask client with auth enabled."""
        import sqlite3

        # Create library DB
        lib_db_path = tmp_path / "audiobooks.db"
        conn = sqlite3.connect(str(lib_db_path))
        schema_path = Path(__file__).parent.parent / "backend" / "schema.sql"
        conn.executescript(schema_path.read_text())
        conn.close()

        # Create auth DB
        auth_db_path = tmp_path / "auth.db"
        auth_key_path = tmp_path / "auth.key"
        auth_db = AuthDatabase(db_path=str(auth_db_path), key_path=str(auth_key_path), is_dev=True)
        auth_db.initialize()

        # Create test user
        user = _create_test_user(auth_db)

        # Create Flask app
        from backend.api_modular import create_app

        app = create_app(
            database_path=str(lib_db_path),
            project_dir=Path(__file__).parent.parent.parent,
            supplements_dir=tmp_path / "supplements",
            auth_db_path=str(auth_db_path),
            auth_key_path=str(auth_key_path),
            auth_dev_mode=True,
        )
        app.config["TESTING"] = True

        # Create a session for the test user
        from auth.models import Session

        _session, raw_token = Session.create_for_user(
            db=auth_db, user_id=user.id, user_agent="pytest", ip_address="127.0.0.1"
        )

        client = app.test_client()
        client.set_cookie("audiobooks_session", raw_token)

        yield client

    def test_get_defaults(self, client):
        """GET /api/user/preferences returns defaults for new user."""
        resp = client.get("/api/user/preferences")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["font_size"] == "16"
        assert data["sort_order"] == "title_asc"
        assert len(data) == len(UserSettingsRepository.DEFAULTS)

    def test_patch_preferences(self, client):
        """PATCH /api/user/preferences updates and returns full settings."""
        resp = client.patch(
            "/api/user/preferences",
            data=json.dumps({"font_size": "20", "playback_speed": "1.5"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["font_size"] == "20"
        assert data["playback_speed"] == "1.5"
        # Defaults still present
        assert data["sort_order"] == "title_asc"

    def test_patch_invalid_body(self, client):
        """PATCH with non-JSON body returns 400."""
        resp = client.patch("/api/user/preferences", data="not json", content_type="text/plain")
        assert resp.status_code == 400

    def test_patch_no_valid_keys(self, client):
        """PATCH with all-invalid keys returns 400."""
        resp = client.patch(
            "/api/user/preferences",
            data=json.dumps({"bad_key": "value"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_delete_preference(self, client):
        """DELETE /api/user/preferences/<key> resets to default."""
        # Set a value first
        client.patch(
            "/api/user/preferences",
            data=json.dumps({"font_size": "20"}),
            content_type="application/json",
        )

        resp = client.delete("/api/user/preferences/font_size")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["value"] == "16"  # default

    def test_delete_invalid_key(self, client):
        """DELETE with invalid key returns 400."""
        resp = client.delete("/api/user/preferences/nonexistent")
        assert resp.status_code == 400

    def test_reset_all(self, client):
        """POST /api/user/preferences/reset clears all custom settings."""
        # Set some values
        client.patch(
            "/api/user/preferences",
            data=json.dumps({"font_size": "20", "contrast": "high"}),
            content_type="application/json",
        )

        resp = client.post("/api/user/preferences/reset")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["preferences"]["font_size"] == "16"  # back to default

    def test_get_defaults_no_auth(self, client):
        """GET /api/user/preferences/defaults works without auth."""
        resp = client.get("/api/user/preferences/defaults")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["font_size"] == "16"

    def test_unauthenticated_returns_401(self, tmp_path):
        """Authenticated endpoints require login."""
        import sqlite3

        lib_db_path = tmp_path / "audiobooks2.db"
        conn = sqlite3.connect(str(lib_db_path))
        schema_path = Path(__file__).parent.parent / "backend" / "schema.sql"
        conn.executescript(schema_path.read_text())
        conn.close()

        auth_db_path = tmp_path / "auth2.db"
        auth_key_path = tmp_path / "auth2.key"
        auth_db = AuthDatabase(db_path=str(auth_db_path), key_path=str(auth_key_path), is_dev=True)
        auth_db.initialize()

        from backend.api_modular import create_app

        app = create_app(
            database_path=str(lib_db_path),
            project_dir=Path(__file__).parent.parent.parent,
            supplements_dir=tmp_path / "supplements2",
            auth_db_path=str(auth_db_path),
            auth_key_path=str(auth_key_path),
            auth_dev_mode=True,
        )
        app.config["TESTING"] = True

        with app.test_client() as c:
            resp = c.get("/api/user/preferences")
            assert resp.status_code in (401, 302)
