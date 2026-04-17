"""
Unit tests for download tracking: POST /api/user/downloads/<id>/complete.

Verifies that the download completion API correctly records downloads,
allows duplicates, persists file format, and returns downloads in history.

These tests exercise the endpoint created in Task 4 (user_state.py) to
validate the integration that the JS fetch/blob frontend will use.
"""

import sys
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth.totp import TOTPAuthenticator  # noqa: E402

# auth_app and auth_temp_dir fixtures come from conftest.py (session-scoped)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def client(auth_app):
    """Create test client using the shared auth app."""
    return auth_app.test_client()


def _login(client, auth_app):
    """Log in as testuser1 and return the authenticated client."""
    auth = TOTPAuthenticator(auth_app.test_user_secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": "testuser1", "code": code})
    assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
    return client


def _login_admin(client, auth_app):
    """Log in as adminuser and return the authenticated client."""
    auth = TOTPAuthenticator(auth_app.admin_secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": "adminuser", "code": code})
    assert resp.status_code == 200, f"Admin login failed: {resp.get_json()}"
    return client


# ============================================================
# Download Completion API Tests
# ============================================================


class TestDownloadCompletionAPI:
    """Tests for POST /api/user/downloads/<id>/complete."""

    def test_complete_records_format(self, client, auth_app):
        """Recording download includes file format in response."""
        _login(client, auth_app)
        resp = client.post("/api/user/downloads/1/complete", json={"file_format": "opus"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["audiobook_id"] == 1
        assert "download_id" in data

    def test_download_appears_in_history(self, client, auth_app):
        """After completing download, it appears in user's download history."""
        _login(client, auth_app)

        # Record a download for book 2
        resp = client.post("/api/user/downloads/2/complete", json={"file_format": "opus"})
        assert resp.status_code == 200

        # Verify it shows up in download history
        resp = client.get("/api/user/downloads")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        book2_downloads = [d for d in data["items"] if str(d["audiobook_id"]) == "2"]
        assert len(book2_downloads) >= 1
        # Verify format was persisted
        assert any(d.get("file_format") == "opus" for d in book2_downloads)

    def test_duplicate_download_allowed(self, client, auth_app):
        """User can download same book multiple times (each recorded)."""
        _login(client, auth_app)

        # Record first download
        resp1 = client.post("/api/user/downloads/3/complete", json={"file_format": "opus"})
        assert resp1.status_code == 200
        id1 = resp1.get_json()["download_id"]

        # Record second download of the same book
        resp2 = client.post("/api/user/downloads/3/complete", json={"file_format": "opus"})
        assert resp2.status_code == 200
        id2 = resp2.get_json()["download_id"]

        # Each download should get a unique ID
        assert id1 != id2

        # Both should appear in history
        resp = client.get("/api/user/downloads")
        data = resp.get_json()
        book3_downloads = [d for d in data["items"] if str(d["audiobook_id"]) == "3"]
        assert len(book3_downloads) >= 2

    def test_nonexistent_book_returns_404(self, client, auth_app):
        """Attempting to record download of non-existent book returns 404."""
        _login(client, auth_app)
        resp = client.post("/api/user/downloads/99999/complete", json={"file_format": "opus"})
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_no_format_still_succeeds(self, client, auth_app):
        """file_format is optional; omitting it still records the download."""
        _login(client, auth_app)
        resp = client.post("/api/user/downloads/1/complete")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_requires_authentication(self, client):
        """Download completion requires a logged-in user."""
        resp = client.post("/api/user/downloads/1/complete", json={"file_format": "opus"})
        assert resp.status_code == 401

    def test_admin_can_record_downloads(self, client, auth_app):
        """Admin users can also record downloads."""
        _login_admin(client, auth_app)
        resp = client.post("/api/user/downloads/1/complete", json={"file_format": "opus"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
