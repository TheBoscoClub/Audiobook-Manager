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
    for i, (book_id, minutes_ago) in enumerate([("1", 120), ("1", 90), ("2", 60), ("3", 30)]):
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
        d = UserDownload(user_id=test_user_id, audiobook_id=book_id, file_format="opus")
        d.save(auth_db)

    # Downloads for admin user
    d = UserDownload(user_id=admin_user_id, audiobook_id="1", file_format="mp3")
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
        resp = client.get(f"/api/admin/activity?user_id={user_id}&type=listen&audiobook_id=1")
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
        assert "total_hours_listened" in data
        assert "total_downloads" in data
        assert "active_users" in data
        assert "top_listened" in data
        assert "top_downloaded" in data

    def test_total_hours_listened(self, client, activity_seeded):
        """total_hours_listened reflects SUM(duration_listened_ms) / 3600000."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        # Seeded: 4 listens of 10min for test user + 2 listens of 60min for admin
        # = 40 + 120 = 160 min = ~2.667 hours minimum (may be more from other tests)
        assert isinstance(data["total_hours_listened"], (int, float))
        assert data["total_hours_listened"] >= 2.5

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
        """top_listened is a list of {audiobook_id, title, total_ms} objects."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        assert isinstance(data["top_listened"], list)
        # May be empty if no rows have title set (e.g. legacy seeded rows
        # from other test files lack the title denormalization).
        if data["top_listened"]:
            item = data["top_listened"][0]
            assert "audiobook_id" in item
            assert "total_ms" in item

    def test_top_downloaded_format(self, client, activity_seeded):
        """top_downloaded is a list of {audiobook_id, title, count} objects."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        assert isinstance(data["top_downloaded"], list)
        if data["top_downloaded"]:
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

    def test_top_listened_sorted_by_total_ms(self, client, activity_seeded):
        """top_listened is sorted by total_ms descending."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        totals = [item["total_ms"] for item in data["top_listened"]]
        assert totals == sorted(totals, reverse=True)

    def test_top_downloaded_sorted_by_count(self, client, activity_seeded):
        """top_downloaded is sorted by count descending."""
        _login_admin(client, activity_seeded)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()
        counts = [item["count"] for item in data["top_downloaded"]]
        assert counts == sorted(counts, reverse=True)


# ============================================================
# Top-listened semantic regression tests (Audiobook-Manager-ptj)
# ============================================================


class TestTopListenedSemantics:
    """Tests covering the v8.3.10.1 semantic shift:

    1. Top Listened sums duration_listened_ms instead of counting slice rows
       (each row in user_listening_history is a ~5-second position-update
       slice, not a real listening session — COUNT(*) inflates wildly).
    2. Top Listened groups by title to collapse stale audiobook_id values
       that occur when the library is re-imported and books get new ids.
    """

    @pytest.fixture
    def isolated_seed(self, auth_app):
        """Seed a known-isolated dataset for a unique title and clean up after.

        We can't reset the shared session-scoped DB, so we use unique titles
        ("ptj-*") that other tests don't touch.
        """
        auth_db = auth_app.auth_db
        user_id = auth_app.test_user_id
        seeded_titles: list[str] = []

        def _seed(title: str, audiobook_id: str, duration_ms: int, count: int = 1):
            seeded_titles.append(title)
            base = datetime.now() - timedelta(hours=24)
            for i in range(count):
                h = UserListeningHistory(
                    user_id=user_id,
                    audiobook_id=audiobook_id,
                    title=title,
                    started_at=base + timedelta(seconds=i * 5),
                    ended_at=base + timedelta(seconds=(i + 1) * 5),
                    position_start_ms=i * 1000,
                    position_end_ms=(i + 1) * 1000,
                    duration_listened_ms=duration_ms,
                )
                h.save(auth_db)

        yield auth_app, _seed

        # Cleanup — remove only the rows we added (by title)
        with auth_db.connection() as conn:
            for title in seeded_titles:
                conn.execute("DELETE FROM user_listening_history WHERE title = ?", (title,))

    def test_top_listened_sums_duration_not_count(self, isolated_seed):
        """5 slices of 10s each = 50000 ms total, not count=5."""
        auth_app, seed = isolated_seed
        title = "ptj-sum-vs-count-test-book"
        # 5 slices, each 10 seconds (10000 ms)
        seed(title=title, audiobook_id="999001", duration_ms=10000, count=5)

        client = auth_app.test_client()
        _login_admin(client, auth_app)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()

        matching = [r for r in data["top_listened"] if r["title"] == title]
        assert len(matching) == 1, f"Expected 1 row for '{title}', got {matching}"
        assert matching[0]["total_ms"] == 50000
        # And critically, the field is total_ms — not "count"
        assert "count" not in matching[0]

    def test_top_listened_collapses_stale_audiobook_ids(self, isolated_seed):
        """Same title under two different audiobook_ids rolls up to ONE entry."""
        auth_app, seed = isolated_seed
        title = "ptj-stale-id-test-book"
        # Mimic a re-import: same title appears under id 999100 (old) and 999200 (new)
        seed(title=title, audiobook_id="999100", duration_ms=20000, count=3)  # 60000 ms
        seed(title=title, audiobook_id="999200", duration_ms=15000, count=2)  # 30000 ms

        client = auth_app.test_client()
        _login_admin(client, auth_app)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()

        matching = [r for r in data["top_listened"] if r["title"] == title]
        # Critical assertion: ONE entry, not two
        assert len(matching) == 1, (
            f"Stale-id collapse failed — expected 1 entry for '{title}', got: {matching}"
        )
        # Sum of all slices: 60000 + 30000 = 90000
        assert matching[0]["total_ms"] == 90000
        # MAX(audiobook_id) should pick the higher (newer) id
        assert matching[0]["audiobook_id"] == "999200"

    def test_total_hours_listened_metric(self, isolated_seed):
        """total_hours_listened is the SUM in hours, with float precision."""
        auth_app, seed = isolated_seed
        title = "ptj-total-hours-test-book"
        # One row of 7,200,000 ms = 2.0 hours
        seed(title=title, audiobook_id="999300", duration_ms=7200000, count=1)

        client = auth_app.test_client()
        _login_admin(client, auth_app)
        resp = client.get("/api/admin/activity/stats")
        data = resp.get_json()

        # The seeded 2 hours must be reflected — but other tests may have added
        # data too, so just check that our 2 hours pushed the total up by >=2.
        # (We can't isolate "total" because it's global. Instead, find our row
        # in top_listened and verify it shows 7,200,000 ms.)
        matching = [r for r in data["top_listened"] if r["title"] == title]
        assert len(matching) == 1
        assert matching[0]["total_ms"] == 7200000
        # And the global total is at least 2 hours (our contribution)
        assert data["total_hours_listened"] >= 2.0
        # And it's a number, not a count
        assert isinstance(data["total_hours_listened"], (int, float))
