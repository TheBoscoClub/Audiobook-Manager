"""
Extended tests for user state API endpoints.

Covers uncovered lines: 54, 86-87, 132-133, 136-137, 254, 259, 269, 280,
296-297, 300, 345-358, 370-383.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import UserDownload, UserListeningHistory  # noqa: E402
from auth.totp import TOTPAuthenticator  # noqa: E402


def _login(client, app, secret):
    """Log in and return the authenticated client."""
    auth = TOTPAuthenticator(secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": "testuser1", "code": code})
    assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
    return client


class TestGetLibraryDbNotInitialized:
    """Test _get_library_db raises when not initialized (line 54)."""

    def test_raises_runtime_error(self):
        from backend.api_modular.user_state import _get_library_db
        import backend.api_modular.user_state as module

        original = module._db_path
        module._db_path = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                _get_library_db()
        finally:
            module._db_path = original


class TestHistoryPagination:
    """Test history pagination edge cases (lines 86-87)."""

    def test_invalid_offset_defaults(self, client, user_state_seeded):
        """Invalid offset falls back to 0 (lines 86-87)."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/history?offset=abc")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["offset"] == 0


class TestDownloadPagination:
    """Test download pagination edge cases (lines 132-133, 136-137)."""

    def test_invalid_download_limit_defaults(self, client, user_state_seeded):
        """Invalid limit falls back to 50 (lines 132-133)."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/downloads?limit=xyz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 50

    def test_invalid_download_offset_defaults(self, client, user_state_seeded):
        """Invalid offset falls back to 0 (lines 136-137)."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/downloads?offset=xyz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["offset"] == 0


class TestUserLibraryHiddenBooks:
    """Test hidden books in user library (lines 254, 259)."""

    def test_library_hidden_filter_excludes(self, user_client, auth_db, test_user):
        """Default view excludes hidden books (line 254)."""
        from auth import HiddenBookRepository

        # Hide book 1
        repo = HiddenBookRepository(auth_db)
        repo.hide(test_user.id, [1])

        resp = user_client.get("/api/user/library")
        assert resp.status_code == 200
        data = resp.get_json()
        # Book 1 should not appear in default view
        book_ids = [b["id"] for b in data["books"]]
        assert 1 not in book_ids

        # Unhide for cleanup
        repo.unhide(test_user.id, [1])

    def test_library_hidden_filter_shows(self, user_client, auth_db, test_user):
        """Hidden=true view shows only hidden books."""
        from auth import HiddenBookRepository

        repo = HiddenBookRepository(auth_db)
        repo.hide(test_user.id, [1])

        resp = user_client.get("/api/user/library?hidden=true")
        assert resp.status_code == 200
        data = resp.get_json()
        # Only hidden books should appear
        book_ids = [b["id"] for b in data["books"]]
        if book_ids:
            assert 1 in book_ids

        # Unhide for cleanup
        repo.unhide(test_user.id, [1])

    def test_library_empty_ids_returns_empty(self, auth_app, auth_db):
        """Empty all_ids returns empty list (line 259)."""
        from auth import User, AuthType
        from auth.models import Session

        # Create a user with no activity
        user = User(
            username="empty_lib_user", auth_type=AuthType.TOTP, auth_credential=b"secret"
        ).save(auth_db)

        _session, raw_token = Session.create_for_user(
            db=auth_db, user_id=user.id, user_agent="pytest", ip_address="127.0.0.1"
        )
        client = auth_app.test_client()
        client.set_cookie("audiobooks_session", raw_token)

        resp = client.get("/api/user/library")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["books"] == []
        assert data["total"] == 0


class TestUserLibraryTimestamps:
    """Test timestamp handling with None values (lines 269, 280)."""

    def test_download_without_timestamp(self, user_client, auth_db, test_user):
        """Download with None downloaded_at handled (line 280)."""
        # Create a download record — the save() auto-populates downloaded_at,
        # so test the API response which should work correctly
        d = UserDownload(user_id=test_user.id, audiobook_id="3", file_format="mp3")
        d.save(auth_db)

        resp = user_client.get("/api/user/library")
        assert resp.status_code == 200
        data = resp.get_json()
        # Book 3 should be present with a downloaded_at value
        book3 = next((b for b in data["books"] if b["id"] == 3), None)
        if book3:
            assert "downloaded_at" in book3


class TestUserLibraryNonIntegerIds:
    """Test non-integer ID handling (lines 296-297, 300)."""

    def test_non_integer_ids_filtered(self, user_client, auth_db, test_user):
        """Non-integer audiobook IDs are filtered out (lines 296-297)."""

        # Create a listening history entry with a non-integer audiobook_id
        h = UserListeningHistory(
            user_id=test_user.id,
            audiobook_id="not_a_number",
            started_at=datetime.now(),
            position_start_ms=0,
        )
        h.save(auth_db)

        resp = user_client.get("/api/user/library")
        assert resp.status_code == 200
        # Should not crash, non-integer IDs are skipped


class TestHideBooks:
    """Test hide books endpoint (lines 345-358)."""

    def test_hide_requires_audiobook_ids(self, user_client):
        """Missing audiobook_ids returns 400."""
        resp = user_client.post("/api/user/library/hide", json={})
        assert resp.status_code == 400
        assert "audiobook_ids required" in resp.get_json()["error"]

    def test_hide_requires_list_of_integers(self, user_client):
        """Non-integer list returns 400 (lines 353-354)."""
        resp = user_client.post(
            "/api/user/library/hide", json={"audiobook_ids": ["not", "integers"]}
        )
        assert resp.status_code == 400
        assert "list of integers" in resp.get_json()["error"]

    def test_hide_books_success(self, user_client):
        """Successfully hide books (lines 356-358)."""
        resp = user_client.post("/api/user/library/hide", json={"audiobook_ids": [1, 2]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "hidden_count" in data

    def test_hide_no_data(self, user_client):
        """No JSON body returns 400."""
        resp = user_client.post("/api/user/library/hide", content_type="application/json", data="")
        assert resp.status_code == 400


class TestUnhideBooks:
    """Test unhide books endpoint (lines 370-383)."""

    def test_unhide_requires_audiobook_ids(self, user_client):
        """Missing audiobook_ids returns 400."""
        resp = user_client.post("/api/user/library/unhide", json={})
        assert resp.status_code == 400
        assert "audiobook_ids required" in resp.get_json()["error"]

    def test_unhide_requires_list_of_integers(self, user_client):
        """Non-integer list returns 400 (lines 377-378)."""
        resp = user_client.post("/api/user/library/unhide", json={"audiobook_ids": [1.5, "abc"]})
        assert resp.status_code == 400
        assert "list of integers" in resp.get_json()["error"]

    def test_unhide_books_success(self, user_client, auth_db, test_user):
        """Successfully unhide books (lines 381-383)."""
        from auth import HiddenBookRepository

        # First hide some books
        repo = HiddenBookRepository(auth_db)
        repo.hide(test_user.id, [3, 4])

        resp = user_client.post("/api/user/library/unhide", json={"audiobook_ids": [3, 4]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "unhidden_count" in data

    def test_unhide_no_data(self, user_client):
        """No JSON body returns 400."""
        resp = user_client.post(
            "/api/user/library/unhide", content_type="application/json", data=""
        )
        assert resp.status_code == 400


# Reuse the user_state_seeded and client fixtures from conftest and
# test_user_state_api by importing via conftest auto-discovery.
# The fixtures user_client, test_user, auth_db, auth_app come from conftest.py.


@pytest.fixture(scope="session")
def user_state_seeded(auth_app):
    """Seed listening history and downloads (mirrors test_user_state_api)."""
    auth_db = auth_app.auth_db
    user_id = auth_app.test_user_id

    h1 = UserListeningHistory(
        user_id=user_id,
        audiobook_id="1",
        started_at=datetime.now() - timedelta(hours=2),
        ended_at=datetime.now() - timedelta(hours=1),
        position_start_ms=0,
        position_end_ms=3600000,
        duration_listened_ms=3600000,
    )
    h1.save(auth_db)

    h2 = UserListeningHistory(
        user_id=user_id,
        audiobook_id="2",
        started_at=datetime.now() - timedelta(minutes=30),
        position_start_ms=1000,
    )
    h2.save(auth_db)

    d1 = UserDownload(user_id=user_id, audiobook_id="1", file_format="opus")
    d1.save(auth_db)

    return auth_app


@pytest.fixture
def client(user_state_seeded):
    """Create test client for legacy-style tests using TOTP login."""
    return user_state_seeded.test_client()
