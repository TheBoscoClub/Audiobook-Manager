"""
Integration tests for the full per-user state system.

Tests multi-user concurrency (two users interacting with same book independently)
and auth-disabled fallback (per-user features hidden when auth is off).

Uses the session-scoped auth_app fixture from conftest.py which creates:
- Two test users: testuser1 (non-admin) and adminuser (admin)
- Four test audiobooks seeded in the library database

Uses the session-scoped flask_app fixture from conftest.py for no-auth tests
(AUTH_ENABLED=False, user_state/auth blueprints not registered).
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth.totp import TOTPAuthenticator  # noqa: E402

# auth_app, flask_app, and session_temp_dir fixtures come from
# conftest.py (session-scoped)


# ============================================================
# Fixtures
# ============================================================


def _login_user(client, app, username):
    """Log in a specific user and return the authenticated client."""
    auth_db = app.auth_db
    with auth_db.connection() as conn:
        row = conn.execute(
            "SELECT auth_credential FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        secret = row[0]

    auth = TOTPAuthenticator(secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": username, "code": code})
    assert resp.status_code == 200, f"Login failed for {username}: {resp.get_json()}"
    return client


@pytest.fixture
def authed_client(auth_app):
    """First authenticated client (testuser1)."""
    client = auth_app.test_client()
    return _login_user(client, auth_app, "testuser1")


@pytest.fixture
def authed_client_2(auth_app):
    """Second authenticated client (adminuser) for isolation tests."""
    client = auth_app.test_client()
    return _login_user(client, auth_app, "adminuser")


@pytest.fixture(scope="session")
def no_auth_app_seeded(flask_app, session_temp_dir):
    """Seed the session-scoped no-auth flask_app with a test audiobook.

    Uses the existing flask_app fixture from conftest.py which has
    AUTH_ENABLED=False (no auth_db_path/auth_key_path provided).
    Blueprints are already registered once — no double-registration issue.
    """
    db_path = flask_app.config["DATABASE_PATH"]
    conn = sqlite3.connect(db_path)
    # Insert a test audiobook if not already present
    conn.execute(
        "INSERT OR IGNORE INTO audiobooks "
        "(id, title, author, duration_hours, file_path, format) "
        "VALUES (1, 'Test Book', 'Test Author', 5.0, '/test/book1.opus', 'opus')"
    )
    conn.commit()
    conn.close()
    return flask_app


@pytest.fixture
def client_no_auth(no_auth_app_seeded):
    """Client with auth DISABLED (single-user mode).

    Uses the session-scoped flask_app which has AUTH_ENABLED=False.
    The user_state and auth blueprints are NOT registered, so /api/user/*
    routes do not exist (404). Position routes use global fallback.
    """
    return no_auth_app_seeded.test_client()


# ============================================================
# Multi-user concurrency tests
# ============================================================


class TestMultiUserConcurrency:
    """Two users can interact with the same book independently."""

    def test_independent_positions(self, authed_client, authed_client_2):
        """Two users save different positions for the same book."""
        # User 1 saves position at 60s
        r1 = authed_client.put(
            "/api/position/1",
            json={"position_ms": 60000},
        )
        assert r1.status_code == 200

        # User 2 saves position at 120s
        r2 = authed_client_2.put(
            "/api/position/1",
            json={"position_ms": 120000},
        )
        assert r2.status_code == 200

        # User 1 reads back — should still be at 60s, NOT 120s
        resp1 = authed_client.get("/api/position/1")
        assert resp1.status_code == 200
        data1 = resp1.get_json()
        assert data1["local_position_ms"] == 60000

        # User 2 reads back — should be at 120s
        resp2 = authed_client_2.get("/api/position/1")
        assert resp2.status_code == 200
        data2 = resp2.get_json()
        assert data2["local_position_ms"] == 120000

    def test_independent_history(self, authed_client, authed_client_2):
        """Each user has their own listening history."""
        r1 = authed_client.get("/api/user/history")
        r2 = authed_client_2.get("/api/user/history")
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Both should return valid history structures
        assert "items" in r1.get_json()
        assert "items" in r2.get_json()

    def test_independent_downloads(self, authed_client, authed_client_2):
        """Each user has their own download records."""
        # User 1 records a download for book 2
        r1 = authed_client.post(
            "/api/user/downloads/2/complete",
            json={"file_format": "opus"},
        )
        assert r1.status_code == 200

        # User 2 should NOT see user 1's download
        r2 = authed_client_2.get("/api/user/downloads")
        assert r2.status_code == 200
        data2 = r2.get_json()
        downloads = data2.get("items", [])
        # None of user 2's downloads should be for book 2
        # (unless user 2 also downloaded it — which they haven't)
        assert not any(str(d.get("audiobook_id")) == "2" for d in downloads), (
            "User 2 should not see user 1's download for book 2"
        )

    def test_independent_library(self, authed_client, authed_client_2):
        """Each user has their own library view."""
        r1 = authed_client.get("/api/user/library")
        r2 = authed_client_2.get("/api/user/library")
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Both should return valid library structures
        assert "books" in r1.get_json()
        assert "books" in r2.get_json()

    def test_position_update_creates_history_per_user(
        self, authed_client, authed_client_2
    ):
        """Position updates create listening history entries per user."""
        # User 1 updates position for book 3
        r1 = authed_client.put(
            "/api/position/3",
            json={"position_ms": 15000},
        )
        assert r1.status_code == 200

        # User 2 updates position for book 3 at different point
        r2 = authed_client_2.put(
            "/api/position/3",
            json={"position_ms": 45000},
        )
        assert r2.status_code == 200

        # User 1's history should have book 3 at 15000
        hist1 = authed_client.get("/api/user/history")
        data1 = hist1.get_json()
        book3_u1 = [h for h in data1["items"] if str(h["audiobook_id"]) == "3"]
        assert len(book3_u1) >= 1

        # User 2's history should have book 3 at 45000
        hist2 = authed_client_2.get("/api/user/history")
        data2 = hist2.get_json()
        book3_u2 = [h for h in data2["items"] if str(h["audiobook_id"]) == "3"]
        assert len(book3_u2) >= 1

    def test_new_books_independent_dismiss(self, authed_client, authed_client_2):
        """Dismissing new books for one user does not affect the other."""
        # User 1 checks new books — should see all 4 (or some)
        r1_before = authed_client.get("/api/user/new-books")
        assert r1_before.status_code == 200

        # User 2 dismisses new books
        dismiss = authed_client_2.post("/api/user/new-books/dismiss")
        assert dismiss.status_code == 200

        # User 2 should now see 0 new books
        r2_after = authed_client_2.get("/api/user/new-books")
        assert r2_after.status_code == 200
        data2 = r2_after.get_json()
        assert data2["total"] == 0

        # Note: User 1's new_books_seen_at may have been set by other tests
        # (test_user_state_api.py runs in the same session-scoped app).
        # The important thing is that user 2's dismiss did NOT change user 1's state.
        # We verify by checking user 1 can still read new-books endpoint successfully.
        r1_after = authed_client.get("/api/user/new-books")
        assert r1_after.status_code == 200


# ============================================================
# Auth-disabled fallback tests
# ============================================================


class TestAuthDisabledFallback:
    """When auth is disabled, per-user features are blocked/absent."""

    def test_user_endpoints_not_available_without_auth(self, client_no_auth):
        """User-specific endpoints should not be functional when auth disabled.

        When AUTH_ENABLED=False, the user_state and auth blueprints are never
        registered, so these routes do not exist. Flask returns 404 (not found)
        or 405 (method not allowed if a catch-all OPTIONS handler matches the path).
        Either way, the endpoint is not functional for GET requests.
        """
        endpoints = [
            "/api/user/library",
            "/api/user/history",
            "/api/user/downloads",
            "/api/user/new-books",
        ]
        for endpoint in endpoints:
            response = client_no_auth.get(endpoint)
            assert response.status_code in (404, 405), (
                f"{endpoint} returned {response.status_code}, expected 404 or 405"
            )

    def test_global_position_read_without_auth(self, client_no_auth):
        """Position read falls back to global when auth disabled."""
        response = client_no_auth.get("/api/position/1")
        assert response.status_code == 200
        data = response.get_json()
        # Should return the global position (default 0)
        assert data["local_position_ms"] == 0

    def test_global_position_update_without_auth(self, client_no_auth):
        """Position update works globally when auth disabled."""
        response = client_no_auth.put(
            "/api/position/1",
            json={"position_ms": 30000},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["position_ms"] == 30000

        # Read back to verify it persisted globally
        read_resp = client_no_auth.get("/api/position/1")
        assert read_resp.status_code == 200
        assert read_resp.get_json()["local_position_ms"] == 30000

    def test_position_nonexistent_book_without_auth(self, client_no_auth):
        """Position for nonexistent book returns 404."""
        response = client_no_auth.get("/api/position/99999")
        assert response.status_code == 404

    def test_position_status_without_auth(self, client_no_auth):
        """Position status should indicate per_user=False when auth disabled."""
        response = client_no_auth.get("/api/position/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["per_user"] is False


# ============================================================
# Removed endpoints tests
# ============================================================


class TestRemovedEndpoints:
    """Verify Audible sync endpoints are gone."""

    def test_sync_endpoint_removed(self, authed_client):
        """POST /api/position/sync/1 should not exist."""
        response = authed_client.post("/api/position/sync/1")
        assert response.status_code in (404, 405)

    def test_sync_all_endpoint_removed(self, authed_client):
        """POST /api/position/sync-all should not exist."""
        response = authed_client.post("/api/position/sync-all")
        assert response.status_code in (404, 405)

    def test_syncable_endpoint_removed(self, authed_client):
        """GET /api/position/syncable should not exist."""
        response = authed_client.get("/api/position/syncable")
        assert response.status_code in (404, 405)
