"""
Unit tests for user state API endpoints.

Tests cover:
- GET /api/user/history — Paginated listening history
- GET /api/user/downloads — Paginated download history
- POST /api/user/downloads/<id>/complete — Record completed download
- GET /api/user/library — Distinct books user has interacted with
- GET /api/user/new-books — Books added after user's new_books_seen_at
- POST /api/user/new-books/dismiss — Update new_books_seen_at
- Authentication enforcement on all endpoints
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import UserDownload, UserListeningHistory  # noqa: E402
from auth.totp import TOTPAuthenticator  # noqa: E402

# auth_app and auth_temp_dir fixtures come from conftest.py (session-scoped)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="session")
def user_state_seeded(auth_app):
    """Seed listening history and downloads into the shared auth_app.

    Runs once per session. Seeds data needed by user state tests.
    Returns the auth_app for convenience.
    """
    auth_db = auth_app.auth_db
    user_id = auth_app.test_user_id

    # A completed session for book 1
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

    # An open session for book 2
    h2 = UserListeningHistory(
        user_id=user_id,
        audiobook_id="2",
        started_at=datetime.now() - timedelta(minutes=30),
        position_start_ms=1000,
    )
    h2.save(auth_db)

    # A download for book 1
    d1 = UserDownload(
        user_id=user_id,
        audiobook_id="1",
        file_format="opus",
    )
    d1.save(auth_db)

    return auth_app


@pytest.fixture
def client(user_state_seeded):
    """Create test client using the shared auth app with seeded data."""
    return user_state_seeded.test_client()


def _login(client, app, secret):
    """Log in and return the authenticated client."""
    auth = TOTPAuthenticator(secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": "testuser1", "code": code})
    assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
    return client


# ============================================================
# Authentication enforcement tests
# ============================================================


class TestUserStateAuth:
    """All user state endpoints require authentication."""

    def test_history_requires_auth(self, client):
        resp = client.get("/api/user/history")
        assert resp.status_code == 401

    def test_downloads_requires_auth(self, client):
        resp = client.get("/api/user/downloads")
        assert resp.status_code == 401

    def test_download_complete_requires_auth(self, client):
        resp = client.post("/api/user/downloads/1/complete")
        assert resp.status_code == 401

    def test_library_requires_auth(self, client):
        resp = client.get("/api/user/library")
        assert resp.status_code == 401

    def test_new_books_requires_auth(self, client):
        resp = client.get("/api/user/new-books")
        assert resp.status_code == 401

    def test_dismiss_new_books_requires_auth(self, client):
        resp = client.post("/api/user/new-books/dismiss")
        assert resp.status_code == 401


# ============================================================
# Listening History tests
# ============================================================


class TestListeningHistory:
    """Tests for GET /api/user/history."""

    def test_returns_history(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "count" in data
        # We seeded 2 history entries
        assert len(data["items"]) >= 2

    def test_pagination(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/history?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["items"]) == 1

    def test_history_invalid_limit(self, client, user_state_seeded):
        """Invalid limit defaults to 50."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/history?limit=abc")
        assert resp.status_code == 200

    def test_history_negative_offset(self, client, user_state_seeded):
        """Negative offset clamps to 0."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/history?offset=-5")
        assert resp.status_code == 200

    def test_history_item_fields(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/history")
        data = resp.get_json()
        item = data["items"][0]
        assert "audiobook_id" in item
        assert "started_at" in item
        assert "position_start_ms" in item


# ============================================================
# Download History tests
# ============================================================


class TestDownloadHistory:
    """Tests for GET /api/user/downloads."""

    def test_returns_downloads(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/downloads")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert len(data["items"]) >= 1

    def test_download_item_fields(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/downloads")
        data = resp.get_json()
        item = data["items"][0]
        assert "audiobook_id" in item
        assert "downloaded_at" in item
        assert "file_format" in item


# ============================================================
# Record download complete tests
# ============================================================


class TestDownloadComplete:
    """Tests for POST /api/user/downloads/<id>/complete."""

    def test_record_download(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.post(
            "/api/user/downloads/3/complete",
            json={"file_format": "opus"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_record_download_nonexistent_book(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.post(
            "/api/user/downloads/99999/complete",
            json={"file_format": "opus"},
        )
        assert resp.status_code == 404

    def test_record_download_no_format(self, client, user_state_seeded):
        """file_format is optional -- should still succeed."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.post("/api/user/downloads/2/complete")
        assert resp.status_code == 200


# ============================================================
# User Library tests
# ============================================================


class TestUserLibrary:
    """Tests for GET /api/user/library."""

    def test_returns_library(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/library")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "books" in data
        # User has history for books 1 & 2, download for book 1
        # Plus downloads recorded in TestDownloadComplete (books 2 & 3)
        assert len(data["books"]) >= 2

    def test_library_includes_metadata(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/library")
        data = resp.get_json()
        # Each book should have metadata from library DB
        book = data["books"][0]
        assert "title" in book
        assert "author" in book

    def test_library_includes_timestamps(self, client, user_state_seeded):
        """Library response should include last_listened_at and
        downloaded_at timestamps."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/library")
        data = resp.get_json()
        # All books should have timestamp fields (even if None)
        for book in data["books"]:
            assert "last_listened_at" in book
            assert "downloaded_at" in book
        # Book 1 has both history and download — both timestamps should be set
        book1 = next((b for b in data["books"] if b["id"] == 1), None)
        if book1:
            assert book1["last_listened_at"] is not None
            assert book1["downloaded_at"] is not None


# ============================================================
# New Books tests
# ============================================================


class TestNewBooks:
    """Tests for GET /api/user/new-books."""

    def test_returns_new_books_first_time(self, client, user_state_seeded):
        """First time: new_books_seen_at is NULL, so all books are 'new'."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.get("/api/user/new-books")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "books" in data
        # All 4 books should be new (no seen_at set yet)
        assert len(data["books"]) == 4

    def test_new_books_after_dismiss(self, client, user_state_seeded):
        """After dismissing, only books added after dismiss time appear."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.post("/api/user/new-books/dismiss")
        assert resp.status_code == 200

        # Now new-books should return 0 (all are now "seen")
        resp = client.get("/api/user/new-books")
        data = resp.get_json()
        assert len(data["books"]) == 0


# ============================================================
# Dismiss New Books tests
# ============================================================


class TestDismissNewBooks:
    """Tests for POST /api/user/new-books/dismiss."""

    def test_dismiss_updates_preferences(self, client, user_state_seeded):
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)
        resp = client.post("/api/user/new-books/dismiss")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "new_books_seen_at" in data


# ============================================================
# Position Sync creates history entry
# ============================================================


class TestPositionSyncHistory:
    """Test that updating position creates/updates listening history."""

    def test_position_update_creates_history(self, client, user_state_seeded):
        """Updating position should create a listening history entry."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)

        # Update position for book 4 (no history yet)
        resp = client.put(
            "/api/position/4",
            json={"position_ms": 5000},
        )
        assert resp.status_code == 200

        # Check that a history entry was created
        resp = client.get("/api/user/history")
        data = resp.get_json()
        book4_entries = [h for h in data["items"] if str(h["audiobook_id"]) == "4"]
        assert len(book4_entries) >= 1

    def test_position_update_updates_existing_session(self, client, user_state_seeded):
        """Updating position for a book with an open session should update it."""
        _login(client, user_state_seeded, user_state_seeded.test_user_secret)

        # Book 2 already has an open session from seeding
        resp = client.put(
            "/api/position/2",
            json={"position_ms": 50000},
        )
        assert resp.status_code == 200

        # Check history -- should still have the same session but updated end position
        resp = client.get("/api/user/history")
        data = resp.get_json()
        book2_entries = [h for h in data["items"] if str(h["audiobook_id"]) == "2"]
        # Should have an entry with position_end_ms updated
        assert any(h.get("position_end_ms") == 50000 for h in book2_entries)
