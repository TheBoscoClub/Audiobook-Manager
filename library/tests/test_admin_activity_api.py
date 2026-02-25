"""
Unit tests for admin activity API endpoints.

Tests cover:
- GET /api/admin/activity — Paginated, filterable activity log
- GET /api/admin/activity/stats — Aggregate activity statistics
- Authentication and authorization enforcement
- Filtering by user_id, type, audiobook_id, date range
- Pagination (limit/offset)
- Input validation (audiobook_id, limit cap)
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
def activity_seeded(auth_app):
    """Seed activity data into the shared auth_app for admin activity tests.

    Creates listening history and downloads across multiple users and books
    to exercise filtering, pagination, and stats aggregation.
    """
    auth_db = auth_app.auth_db
    test_user_id = auth_app.test_user_id
    admin_user_id = auth_app.admin_user_id

    # Listening sessions for test user
    for i, (book_id, minutes_ago) in enumerate(
        [("1", 120), ("1", 90), ("2", 60), ("3", 30)]
    ):
        h = UserListeningHistory(
            user_id=test_user_id,
            audiobook_id=book_id,
            started_at=datetime.now() - timedelta(minutes=minutes_ago),
            ended_at=datetime.now() - timedelta(minutes=minutes_ago - 10),
            position_start_ms=i * 1000,
            position_end_ms=(i + 1) * 1000,
            duration_listened_ms=10 * 60 * 1000,
        )
        h.save(auth_db)

    # Listening sessions for admin user
    for book_id in ["1", "2"]:
        h = UserListeningHistory(
            user_id=admin_user_id,
            audiobook_id=book_id,
            started_at=datetime.now() - timedelta(hours=5),
            ended_at=datetime.now() - timedelta(hours=4),
            position_start_ms=0,
            position_end_ms=5000,
            duration_listened_ms=3600000,
        )
        h.save(auth_db)

    # Downloads for test user
    for book_id in ["1", "2", "3"]:
        d = UserDownload(
            user_id=test_user_id,
            audiobook_id=book_id,
            file_format="opus",
        )
        d.save(auth_db)

    # Downloads for admin user
    d = UserDownload(
        user_id=admin_user_id,
        audiobook_id="1",
        file_format="mp3",
    )
    d.save(auth_db)

    return auth_app


@pytest.fixture
def client(activity_seeded):
    """Create test client using the shared auth app with seeded activity data."""
    return activity_seeded.test_client()


def _login_admin(client, app):
    """Log in as admin and return the authenticated client."""
    auth = TOTPAuthenticator(app.admin_secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": "adminuser", "code": code})
    assert resp.status_code == 200, f"Admin login failed: {resp.get_json()}"
    return client


def _login_user(client, app):
    """Log in as regular user and return the authenticated client."""
    auth = TOTPAuthenticator(app.test_user_secret)
    code = auth.current_code()
    resp = client.post("/auth/login", json={"username": "testuser1", "code": code})
    assert resp.status_code == 200, f"User login failed: {resp.get_json()}"
    return client


# ============================================================
# Authentication & Authorization tests
# ============================================================


class TestAdminActivityAuth:
    """Admin activity endpoints require admin privileges."""

    def test_activity_requires_auth(self, client):
        """Unauthenticated request returns 401."""
        resp = client.get("/api/admin/activity")
        assert resp.status_code == 401

    def test_activity_requires_admin(self, client, activity_seeded):
        """Non-admin user gets 403."""
        _login_user(client, activity_seeded)
        resp = client.get("/api/admin/activity")
        assert resp.status_code == 403

    def test_stats_requires_auth(self, client):
        """Unauthenticated request to stats returns 401."""
        resp = client.get("/api/admin/activity/stats")
        assert resp.status_code == 401

    def test_stats_requires_admin(self, client, activity_seeded):
        """Non-admin user gets 403 on stats."""
        _login_user(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        assert resp.status_code == 403


# ============================================================
# Activity Log tests
# ============================================================


class TestActivityLog:
    """Tests for GET /api/admin/activity."""

    def test_returns_activity(self, client, activity_seeded):
        """Admin can retrieve the activity log."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "activity" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert len(data["activity"]) > 0
        assert data["total"] >= len(data["activity"])

    def test_activity_item_fields(self, client, activity_seeded):
        """Each activity item has the required fields including title."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity")
        data = resp.get_json()
        item = data["activity"][0]
        assert "type" in item
        assert item["type"] in ("listen", "download")
        assert "user_id" in item
        assert "username" in item
        assert "audiobook_id" in item
        assert "title" in item
        assert "timestamp" in item

    def test_activity_title_resolved(self, client, activity_seeded):
        """Activity items resolve audiobook titles from the library DB."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity")
        data = resp.get_json()
        # At least some items should have non-null titles (test DB has books 1-3)
        titles = [item["title"] for item in data["activity"] if item["title"]]
        assert len(titles) > 0, "Expected at least one resolved title"

    def test_listen_type_has_duration(self, client, activity_seeded):
        """Listen-type items include duration_listened_ms."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?type=listen")
        data = resp.get_json()
        assert len(data["activity"]) > 0
        for item in data["activity"]:
            assert item["type"] == "listen"
            assert "duration_listened_ms" in item

    def test_download_type_has_format(self, client, activity_seeded):
        """Download-type items include file_format."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?type=download")
        data = resp.get_json()
        assert len(data["activity"]) > 0
        for item in data["activity"]:
            assert item["type"] == "download"
            assert "file_format" in item

    def test_combined_timeline(self, client, activity_seeded):
        """Without type filter, both listen and download events appear."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity")
        data = resp.get_json()
        types = {item["type"] for item in data["activity"]}
        assert "listen" in types
        assert "download" in types

    def test_sorted_by_timestamp_desc(self, client, activity_seeded):
        """Activity items are sorted by timestamp descending (newest first)."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity")
        data = resp.get_json()
        timestamps = [item["timestamp"] for item in data["activity"]]
        assert timestamps == sorted(timestamps, reverse=True)

    # -- Pagination tests --

    def test_pagination_limit(self, client, activity_seeded):
        """Limit controls number of items returned."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?limit=2")
        data = resp.get_json()
        assert len(data["activity"]) == 2
        assert data["limit"] == 2

    def test_pagination_offset(self, client, activity_seeded):
        """Offset skips initial items."""
        _login_admin(client, activity_seeded)
        # Get first page
        resp1 = client.get("/api/admin/activity?limit=2&offset=0")
        data1 = resp1.get_json()
        # Get second page
        resp2 = client.get("/api/admin/activity?limit=2&offset=2")
        data2 = resp2.get_json()
        # Pages should not overlap
        ids1 = {(i["type"], i["timestamp"]) for i in data1["activity"]}
        ids2 = {(i["type"], i["timestamp"]) for i in data2["activity"]}
        assert ids1.isdisjoint(ids2)

    def test_invalid_limit_defaults(self, client, activity_seeded):
        """Invalid limit defaults to 50."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?limit=abc")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 50

    def test_negative_offset_clamps(self, client, activity_seeded):
        """Negative offset clamps to 0."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?offset=-5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["offset"] == 0

    def test_limit_capped_at_200(self, client, activity_seeded):
        """Limit is capped at 200 even if a higher value is requested."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?limit=500")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["limit"] == 200

    def test_total_reflects_all_matching(self, client, activity_seeded):
        """Total count reflects all matching items, not just the page."""
        _login_admin(client, activity_seeded)
        # Get total with no pagination
        resp_all = client.get("/api/admin/activity?limit=200")
        total_all = resp_all.get_json()["total"]
        # Get first page of 2
        resp_page = client.get("/api/admin/activity?limit=2")
        data_page = resp_page.get_json()
        # Total should be the same regardless of page size
        assert data_page["total"] == total_all
        assert data_page["total"] > len(data_page["activity"])

    # -- Filter tests --

    def test_filter_by_user_id(self, client, activity_seeded):
        """Filter activity to a specific user."""
        _login_admin(client, activity_seeded)
        user_id = activity_seeded.test_user_id
        resp = client.get(f"/api/admin/activity?user_id={user_id}")
        data = resp.get_json()
        assert len(data["activity"]) > 0
        for item in data["activity"]:
            assert item["user_id"] == user_id

    def test_filter_by_type_listen(self, client, activity_seeded):
        """Filter to listen events only."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?type=listen")
        data = resp.get_json()
        for item in data["activity"]:
            assert item["type"] == "listen"

    def test_filter_by_type_download(self, client, activity_seeded):
        """Filter to download events only."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?type=download")
        data = resp.get_json()
        for item in data["activity"]:
            assert item["type"] == "download"

    def test_filter_by_audiobook_id(self, client, activity_seeded):
        """Filter activity for a specific audiobook."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?audiobook_id=1")
        data = resp.get_json()
        assert len(data["activity"]) > 0
        for item in data["activity"]:
            assert item["audiobook_id"] == "1"

    def test_filter_by_date_range(self, client, activity_seeded):
        """Filter activity within a date range."""
        _login_admin(client, activity_seeded)
        now = datetime.now()
        from_date = (now - timedelta(hours=6)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")
        resp = client.get(f"/api/admin/activity?from={from_date}&to={to_date}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["activity"]) > 0

    def test_invalid_date_ignored(self, client, activity_seeded):
        """Invalid date params are ignored (no error)."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?from=not-a-date&to=also-bad")
        assert resp.status_code == 200

    def test_filter_combination(self, client, activity_seeded):
        """Multiple filters can be combined."""
        _login_admin(client, activity_seeded)
        user_id = activity_seeded.test_user_id
        resp = client.get(
            f"/api/admin/activity?user_id={user_id}&type=listen&audiobook_id=1"
        )
        data = resp.get_json()
        for item in data["activity"]:
            assert item["user_id"] == user_id
            assert item["type"] == "listen"
            assert item["audiobook_id"] == "1"

    def test_filter_invalid_type_returns_empty(self, client, activity_seeded):
        """Invalid type filter returns empty results."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?type=invalid")
        data = resp.get_json()
        assert len(data["activity"]) == 0

    def test_invalid_audiobook_id_returns_400(self, client, activity_seeded):
        """Non-numeric audiobook_id returns 400 with error message."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity?audiobook_id=abc")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "audiobook_id" in data["error"]


# ============================================================
# Activity Stats tests
# ============================================================


class TestActivityStats:
    """Tests for GET /api/admin/activity/stats."""

    def test_returns_stats(self, client, activity_seeded):
        """Admin can retrieve aggregate stats."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_listens" in data
        assert "total_downloads" in data
        assert "active_users" in data
        assert "top_listened" in data
        assert "top_downloaded" in data

    def test_total_listens_count(self, client, activity_seeded):
        """total_listens reflects the seeded data."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        # We seeded 4 listens for test user + 2 for admin = 6 minimum
        # (may be more from other test files' seeded data)
        assert data["total_listens"] >= 6

    def test_total_downloads_count(self, client, activity_seeded):
        """total_downloads reflects the seeded data."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        # We seeded 3 downloads for test user + 1 for admin = 4 minimum
        assert data["total_downloads"] >= 4

    def test_active_users_count(self, client, activity_seeded):
        """active_users counts distinct users with any activity."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        # Both test user and admin have activity
        assert data["active_users"] >= 2

    def test_top_listened_format(self, client, activity_seeded):
        """top_listened is a list of {audiobook_id, title, count} objects."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        assert isinstance(data["top_listened"], list)
        assert len(data["top_listened"]) > 0
        item = data["top_listened"][0]
        assert "audiobook_id" in item
        assert "count" in item

    def test_top_downloaded_format(self, client, activity_seeded):
        """top_downloaded is a list of {audiobook_id, title, count} objects."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        assert isinstance(data["top_downloaded"], list)
        assert len(data["top_downloaded"]) > 0
        item = data["top_downloaded"][0]
        assert "audiobook_id" in item
        assert "count" in item

    def test_top_listened_limit(self, client, activity_seeded):
        """top_listened returns at most 10 items."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        assert len(data["top_listened"]) <= 10

    def test_top_downloaded_limit(self, client, activity_seeded):
        """top_downloaded returns at most 10 items."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        assert len(data["top_downloaded"]) <= 10

    def test_top_listened_sorted_by_count(self, client, activity_seeded):
        """top_listened is sorted by count descending."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        counts = [item["count"] for item in data["top_listened"]]
        assert counts == sorted(counts, reverse=True)

    def test_top_downloaded_sorted_by_count(self, client, activity_seeded):
        """top_downloaded is sorted by count descending."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        counts = [item["count"] for item in data["top_downloaded"]]
        assert counts == sorted(counts, reverse=True)
