"""
Tests for periodicals API module.

This module provides endpoints for browsing non-audiobook content from Audible
(podcasts, news, shows, meditation series, documentaries, etc.).
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.conftest import LIBRARY_DIR

# Periodicals migration file paths
PERIODICALS_MIGRATION = LIBRARY_DIR / "backend" / "migrations" / "006_periodicals.sql"
PARENT_ASIN_MIGRATION = (
    LIBRARY_DIR / "backend" / "migrations" / "008_periodicals_parent_asin.sql"
)


def apply_periodicals_migration(db_path: Path) -> None:
    """Apply periodicals migrations to test database if not already applied."""
    conn = sqlite3.connect(db_path)
    # Check if table exists
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='periodicals'"
    ).fetchone()
    if not result:
        with open(PERIODICALS_MIGRATION) as f:
            conn.executescript(f.read())

    # Check if parent_asin column exists
    columns = conn.execute("PRAGMA table_info(periodicals)").fetchall()
    column_names = [col[1] for col in columns]
    if "parent_asin" not in column_names:
        with open(PARENT_ASIN_MIGRATION) as f:
            conn.executescript(f.read())
    conn.close()


@pytest.fixture(scope="session")
def periodicals_db_path(session_temp_dir):
    """Get the path to the shared test database and add periodicals tables."""
    db_path = session_temp_dir / "test_audiobooks.db"
    # Apply periodicals migration to the shared test database
    apply_periodicals_migration(db_path)
    return db_path


@pytest.fixture
def clean_periodicals(periodicals_db_path):
    """Clean the periodicals tables before each test."""
    conn = sqlite3.connect(periodicals_db_path)
    conn.execute("DELETE FROM periodicals")
    conn.execute("DELETE FROM periodicals_sync_status")
    conn.commit()
    conn.close()
    return periodicals_db_path


class TestValidateAsin:
    """Test the validate_asin function."""

    def test_valid_asin_returns_true(self):
        """Test valid ASIN format passes validation."""
        from backend.api_modular.periodicals import validate_asin

        assert validate_asin("B08XYZ1234") is True
        assert validate_asin("0123456789") is True
        assert validate_asin("ABCDEFGHIJ") is True

    def test_invalid_asin_too_short(self):
        """Test short ASIN fails validation."""
        from backend.api_modular.periodicals import validate_asin

        assert validate_asin("B12345") is False

    def test_invalid_asin_too_long(self):
        """Test long ASIN fails validation."""
        from backend.api_modular.periodicals import validate_asin

        assert validate_asin("B1234567890ABC") is False

    def test_invalid_asin_lowercase(self):
        """Test lowercase ASIN fails validation."""
        from backend.api_modular.periodicals import validate_asin

        assert validate_asin("b08xyz1234") is False

    def test_invalid_asin_special_chars(self):
        """Test ASIN with special characters fails."""
        from backend.api_modular.periodicals import validate_asin

        assert validate_asin("B08XYZ-123") is False
        assert validate_asin("B08_XYZ123") is False

    def test_empty_asin(self):
        """Test empty string fails validation."""
        from backend.api_modular.periodicals import validate_asin

        assert validate_asin("") is False


class TestListPeriodicals:
    """Test the list_periodicals route."""

    def test_returns_empty_list_initially(self, flask_app, clean_periodicals):
        """Test returns empty list when no periodicals exist."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals")

        assert response.status_code == 200
        data = response.get_json()
        assert data["periodicals"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    def test_returns_periodicals(self, flask_app, clean_periodicals):
        """Test returns periodicals from database."""
        # Insert test data
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, author, category, content_type, runtime_minutes)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("B001234567", "Test Podcast", "Test Host", "podcast", "Podcast", 60),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals")

        assert response.status_code == 200
        data = response.get_json()
        assert len(data["periodicals"]) == 1
        assert data["periodicals"][0]["title"] == "Test Podcast"
        assert data["total"] == 1

    def test_filters_by_category(self, flask_app, clean_periodicals):
        """Test filters periodicals by category."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
            ("B001111111", "Podcast 1", "podcast"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
            ("B002222222", "News Item", "news"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?category=podcast")

        data = response.get_json()
        assert len(data["periodicals"]) == 1
        assert data["periodicals"][0]["category"] == "podcast"

    def test_sort_by_title(self, flask_app, clean_periodicals):
        """Test sorting by title."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
            ("B001111111", "Zebra Show", "podcast"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
            ("B002222222", "Alpha Show", "podcast"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?sort=title")

        data = response.get_json()
        assert data["periodicals"][0]["title"] == "Alpha Show"
        assert data["periodicals"][1]["title"] == "Zebra Show"

    def test_sort_by_runtime(self, flask_app, clean_periodicals):
        """Test sorting by runtime (descending)."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, runtime_minutes) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Short", "podcast", 30),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, runtime_minutes) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Long", "podcast", 120),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?sort=runtime")

        data = response.get_json()
        assert data["periodicals"][0]["title"] == "Long"
        assert data["periodicals"][1]["title"] == "Short"

    def test_sort_by_category(self, flask_app, clean_periodicals):
        """Test sorting by category."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
            ("B001111111", "News A", "news"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
            ("B002222222", "Documentary B", "documentary"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?sort=category")

        data = response.get_json()
        # documentary < news alphabetically
        assert data["periodicals"][0]["category"] == "documentary"
        assert data["periodicals"][1]["category"] == "news"

    def test_pagination(self, flask_app, clean_periodicals):
        """Test pagination works correctly."""
        conn = sqlite3.connect(clean_periodicals)
        for i in range(10):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
                (f"B00{i:07d}", f"Podcast {i}", "podcast"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?page=2&per_page=3")

        data = response.get_json()
        assert len(data["periodicals"]) == 3
        assert data["page"] == 2
        assert data["per_page"] == 3
        assert data["total"] == 10
        assert data["total_pages"] == 4

    def test_max_per_page_limit(self, flask_app, clean_periodicals):
        """Test per_page is capped at 200."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?per_page=500")

        data = response.get_json()
        assert data["per_page"] == 200

    def test_min_per_page_limit(self, flask_app, clean_periodicals):
        """Test per_page has minimum of 1."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?per_page=0")

        data = response.get_json()
        assert data["per_page"] == 1


class TestPeriodicalDetails:
    """Test the periodical_details route."""

    def test_returns_periodical_details(self, flask_app, clean_periodicals):
        """Test returns full details for a periodical."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, author, narrator, category, content_type,
            runtime_minutes, release_date, description, cover_url, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "B001234567",
                "Test Podcast",
                "Test Host",
                "Test Narrator",
                "podcast",
                "Podcast",
                60,
                "2024-01-15",
                "A great podcast",
                "https://example.com/cover.jpg",
                0,
                0,
            ),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B001234567")

        assert response.status_code == 200
        data = response.get_json()
        assert data["asin"] == "B001234567"
        assert data["title"] == "Test Podcast"
        assert data["narrator"] == "Test Narrator"
        assert data["description"] == "A great podcast"

    def test_returns_404_for_missing_asin(self, flask_app, clean_periodicals):
        """Test returns 404 when ASIN not found."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B999999999")

        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data

    def test_returns_400_for_invalid_asin(self, flask_app):
        """Test returns 400 for invalid ASIN format."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/invalid")

        assert response.status_code == 400
        data = response.get_json()
        assert "Invalid ASIN" in data["error"]


class TestQueueDownloads:
    """Test the queue_downloads route."""

    def test_queues_download(self, flask_app, clean_periodicals):
        """Test successfully queues a download."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Test Podcast", "podcast", 0, 0),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B001234567"]},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["queued"] == 1
        assert data["total_requested"] == 1

    def test_skips_already_downloaded(self, flask_app, clean_periodicals):
        """Test skips items that are already downloaded."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Downloaded Item", "podcast", 1, 0),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B001234567"]},
            )

        data = response.get_json()
        assert data["queued"] == 0
        assert data["already_downloaded"] == 1

    def test_skips_already_queued(self, flask_app, clean_periodicals):
        """Test skips items already in queue."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Queued Item", "podcast", 0, 1),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B001234567"]},
            )

        data = response.get_json()
        assert data["queued"] == 0
        assert data["already_queued"] == 1

    def test_skips_nonexistent_asin(self, flask_app, clean_periodicals):
        """Test skips ASINs that don't exist in database."""
        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B999999999"]},
            )

        data = response.get_json()
        # Non-existent ASINs are silently skipped
        assert data["queued"] == 0

    def test_returns_400_without_asins(self, flask_app):
        """Test returns 400 when asins not provided."""
        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={},
            )

        assert response.status_code == 400
        data = response.get_json()
        assert "asins" in data["error"]

    def test_returns_400_for_empty_asins(self, flask_app):
        """Test returns 400 for empty ASIN list."""
        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": []},
            )

        assert response.status_code == 400
        assert "Empty" in response.get_json()["error"]

    def test_returns_400_for_invalid_asins(self, flask_app):
        """Test returns 400 when any ASIN is invalid."""
        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B001234567", "invalid"]},
            )

        assert response.status_code == 400
        data = response.get_json()
        assert "Invalid" in data["error"]

    def test_applies_priority_high(self, flask_app, clean_periodicals):
        """Test applies high download priority."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Priority Item", "podcast", 0, 0),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B001234567"], "priority": "high"},
            )

        assert response.status_code == 200

        # Verify priority was set
        conn = sqlite3.connect(clean_periodicals)
        row = conn.execute(
            "SELECT download_priority FROM periodicals WHERE asin = ?",
            ("B001234567",),
        ).fetchone()
        conn.close()
        assert row[0] == 10  # high priority = 10

    def test_applies_priority_low(self, flask_app, clean_periodicals):
        """Test applies low download priority."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Low Priority Item", "podcast", 0, 0),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            client.post(
                "/api/v1/periodicals/download",
                json={"asins": ["B001234567"], "priority": "low"},
            )

        conn = sqlite3.connect(clean_periodicals)
        row = conn.execute(
            "SELECT download_priority FROM periodicals WHERE asin = ?",
            ("B001234567",),
        ).fetchone()
        conn.close()
        assert row[0] == -10  # low priority = -10


class TestCancelDownload:
    """Test the cancel_download route."""

    def test_cancels_queued_download(self, flask_app, clean_periodicals):
        """Test successfully cancels a queued download."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested, download_priority)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("B001234567", "Queued Item", "podcast", 0, 1, 10),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.delete("/api/v1/periodicals/download/B001234567")

        assert response.status_code == 200
        data = response.get_json()
        assert data["cancelled"] == "B001234567"

        # Verify it was reset
        conn = sqlite3.connect(clean_periodicals)
        row = conn.execute(
            "SELECT download_requested, download_priority FROM periodicals WHERE asin = ?",
            ("B001234567",),
        ).fetchone()
        conn.close()
        assert row[0] == 0
        assert row[1] == 0

    def test_returns_404_for_not_queued(self, flask_app, clean_periodicals):
        """Test returns 404 if item is not in queue."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Not Queued", "podcast", 0, 0),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.delete("/api/v1/periodicals/download/B001234567")

        assert response.status_code == 404

    def test_returns_404_for_already_downloaded(self, flask_app, clean_periodicals):
        """Test returns 404 if item is already downloaded."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested)
            VALUES (?, ?, ?, ?, ?)""",
            ("B001234567", "Downloaded", "podcast", 1, 1),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.delete("/api/v1/periodicals/download/B001234567")

        assert response.status_code == 404

    def test_returns_400_for_invalid_asin(self, flask_app):
        """Test returns 400 for invalid ASIN format."""
        with flask_app.test_client() as client:
            response = client.delete("/api/v1/periodicals/download/invalid")

        assert response.status_code == 400


class TestGetQueue:
    """Test the get_queue route."""

    def test_returns_empty_queue(self, flask_app, clean_periodicals):
        """Test returns empty queue initially."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/queue")

        assert response.status_code == 200
        data = response.get_json()
        assert data["queue"] == []
        assert data["total"] == 0

    def test_returns_queued_items(self, flask_app, clean_periodicals):
        """Test returns items in download queue."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, content_type, is_downloaded, download_requested, download_priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("B001234567", "Queued Podcast", "podcast", "Podcast", 0, 1, 5),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/queue")

        data = response.get_json()
        assert len(data["queue"]) == 1
        assert data["queue"][0]["asin"] == "B001234567"
        assert data["queue"][0]["title"] == "Queued Podcast"
        assert data["queue"][0]["priority"] == 5

    def test_queue_sorted_by_priority(self, flask_app, clean_periodicals):
        """Test queue is sorted by priority descending."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested, download_priority)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("B001111111", "Low Priority", "podcast", 0, 1, -10),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, is_downloaded, download_requested, download_priority)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("B002222222", "High Priority", "podcast", 0, 1, 10),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/queue")

        data = response.get_json()
        assert data["queue"][0]["title"] == "High Priority"
        assert data["queue"][1]["title"] == "Low Priority"


class TestSyncStatusSSE:
    """Test the sync_status_sse route.

    Note: Full SSE generator testing is skipped due to Flask's request context
    limitations - generators execute outside the app context when consumed.
    The route and response type are tested indirectly through other mechanisms.
    """

    @pytest.mark.skip(
        reason="SSE generator runs outside request context in test client"
    )
    def test_returns_sse_stream(self, flask_app, clean_periodicals):
        """Test returns Server-Sent Events stream."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/sync/status")

        assert response.status_code == 200
        assert response.mimetype == "text/event-stream"

    @pytest.mark.skip(
        reason="SSE generator runs outside request context in test client"
    )
    def test_returns_no_sync_history(self, flask_app, clean_periodicals):
        """Test returns no_sync_history when no syncs exist."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/sync/status")

        data = response.get_data(as_text=True)
        assert "no_sync_history" in data

    @pytest.mark.skip(
        reason="SSE generator runs outside request context in test client"
    )
    def test_returns_sync_status(self, flask_app, clean_periodicals):
        """Test returns current sync status."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals_sync_status
            (sync_id, status, started_at, total_parents, processed_parents, total_episodes, new_episodes)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("test-uuid", "running", "2024-01-15T10:00:00", 10, 5, 50, 10),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/sync/status")

        data = response.get_data(as_text=True)
        assert "running" in data
        assert "test-uuid" in data

    @pytest.mark.skip(
        reason="SSE generator runs outside request context in test client"
    )
    def test_sse_headers(self, flask_app, clean_periodicals):
        """Test SSE response has correct headers."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/sync/status")

        assert response.headers.get("Cache-Control") == "no-cache"
        assert response.headers.get("Connection") == "keep-alive"
        assert response.headers.get("X-Accel-Buffering") == "no"


class TestTriggerSync:
    """Test the trigger_sync route."""

    @patch("backend.api_modular.periodicals.subprocess.Popen")
    def test_triggers_sync(self, mock_popen, flask_app):
        """Test successfully triggers sync."""
        mock_popen.return_value = MagicMock()

        with flask_app.test_client() as client:
            response = client.post("/api/v1/periodicals/sync/trigger")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "started"
        mock_popen.assert_called_once()

    @patch("backend.api_modular.periodicals.subprocess.Popen")
    def test_triggers_sync_with_asin(self, mock_popen, flask_app):
        """Test triggers sync for specific ASIN."""
        mock_popen.return_value = MagicMock()

        with flask_app.test_client() as client:
            response = client.post("/api/v1/periodicals/sync/trigger?asin=B001234567")

        data = response.get_json()
        assert data["asin"] == "B001234567"

        # Verify --asin was passed
        cmd = mock_popen.call_args[0][0]
        assert "--asin" in cmd
        assert "B001234567" in cmd

    @patch("backend.api_modular.periodicals.subprocess.Popen")
    def test_triggers_sync_with_force(self, mock_popen, flask_app):
        """Test triggers forced sync."""
        mock_popen.return_value = MagicMock()

        with flask_app.test_client() as client:
            response = client.post("/api/v1/periodicals/sync/trigger?force=true")

        data = response.get_json()
        assert data["force"] is True

        # Verify --force was passed
        cmd = mock_popen.call_args[0][0]
        assert "--force" in cmd

    def test_returns_400_for_invalid_asin(self, flask_app):
        """Test returns 400 for invalid ASIN."""
        with flask_app.test_client() as client:
            response = client.post("/api/v1/periodicals/sync/trigger?asin=invalid")

        assert response.status_code == 400

    @patch("backend.api_modular.periodicals.subprocess.Popen")
    def test_returns_500_on_process_error(self, mock_popen, flask_app):
        """Test returns 500 when subprocess fails."""
        mock_popen.side_effect = Exception("Process failed")

        with flask_app.test_client() as client:
            response = client.post("/api/v1/periodicals/sync/trigger")

        assert response.status_code == 500
        data = response.get_json()
        assert "error" in data


class TestListCategories:
    """Test the list_categories route."""

    def test_returns_empty_when_no_data(self, flask_app, clean_periodicals):
        """Test returns empty categories list when no data."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/categories")

        assert response.status_code == 200
        data = response.get_json()
        assert data["categories"] == []

    def test_returns_categories_with_counts(self, flask_app, clean_periodicals):
        """Test returns categories with item counts."""
        conn = sqlite3.connect(clean_periodicals)
        # Add 3 podcasts and 2 news items
        for i in range(3):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
                (f"B00{i:07d}", f"Podcast {i}", "podcast"),
            )
        for i in range(2):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category) VALUES (?, ?, ?)""",
                (f"B01{i:07d}", f"News {i}", "news"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/categories")

        data = response.get_json()
        assert len(data["categories"]) == 2
        # Should be sorted by count descending
        assert data["categories"][0]["category"] == "podcast"
        assert data["categories"][0]["count"] == 3
        assert data["categories"][1]["category"] == "news"
        assert data["categories"][1]["count"] == 2


class TestAsinPatternConstant:
    """Test the ASIN_PATTERN constant."""

    def test_pattern_matches_valid_asins(self):
        """Test regex pattern matches valid ASINs."""
        from backend.api_modular.periodicals import ASIN_PATTERN

        assert ASIN_PATTERN.match("B001234567") is not None
        assert ASIN_PATTERN.match("0123456789") is not None
        assert ASIN_PATTERN.match("ABCDEFGHIJ") is not None

    def test_pattern_rejects_invalid_asins(self):
        """Test regex pattern rejects invalid ASINs."""
        from backend.api_modular.periodicals import ASIN_PATTERN

        assert ASIN_PATTERN.match("B12345") is None  # too short
        assert ASIN_PATTERN.match("B12345678901") is None  # too long
        assert ASIN_PATTERN.match("b001234567") is None  # lowercase


class TestEnvironmentVariable:
    """Test AUDIOBOOKS_HOME environment variable handling."""

    def test_uses_environment_variable(self, monkeypatch):
        """Test uses AUDIOBOOKS_HOME from environment."""
        monkeypatch.setenv("AUDIOBOOKS_HOME", "/custom/path")

        # Need to reload the module to pick up the new env var
        import importlib

        from backend.api_modular import periodicals

        importlib.reload(periodicals)

        assert periodicals._audiobooks_home == "/custom/path"

        # Reset to default
        monkeypatch.delenv("AUDIOBOOKS_HOME", raising=False)
        importlib.reload(periodicals)

    def test_uses_default_when_not_set(self, monkeypatch):
        """Test uses default path when env var not set."""
        monkeypatch.delenv("AUDIOBOOKS_HOME", raising=False)

        import importlib

        from backend.api_modular import periodicals

        importlib.reload(periodicals)

        assert periodicals._audiobooks_home == "/opt/audiobooks"


class TestParentChildHierarchy:
    """Test parent/child relationship functionality."""

    def test_parent_has_null_parent_asin(self, flask_app, clean_periodicals):
        """Test parent items have parent_asin=NULL."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin)
            VALUES (?, ?, ?, ?)""",
            ("B001234567", "Parent Podcast", "podcast", None),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B001234567")

        data = response.get_json()
        assert data["parent_asin"] is None

    def test_episode_has_parent_asin_set(self, flask_app, clean_periodicals):
        """Test episode items have parent_asin set."""
        conn = sqlite3.connect(clean_periodicals)
        # Insert parent
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin)
            VALUES (?, ?, ?, ?)""",
            ("B001234567", "Parent Podcast", "podcast", None),
        )
        # Insert episode
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin, content_delivery_type)
            VALUES (?, ?, ?, ?, ?)""",
            ("B009876543", "Episode 1", "podcast", "B001234567", "PodcastEpisode"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B009876543")

        data = response.get_json()
        assert data["parent_asin"] == "B001234567"
        assert data["content_delivery_type"] == "PodcastEpisode"

    def test_filter_by_type_parents(self, flask_app, clean_periodicals):
        """Test filtering by type=parents returns only parent items."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent 1", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Episode", "podcast", "B001111111"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?type=parents")

        data = response.get_json()
        assert len(data["periodicals"]) == 1
        assert data["periodicals"][0]["asin"] == "B001111111"

    def test_filter_by_type_episodes(self, flask_app, clean_periodicals):
        """Test filtering by type=episodes returns only episode items."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Episode 1", "podcast", "B001111111"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?type=episodes")

        data = response.get_json()
        assert len(data["periodicals"]) == 1
        assert data["periodicals"][0]["asin"] == "B002222222"

    def test_filter_by_parent_asin(self, flask_app, clean_periodicals):
        """Test filtering episodes by parent_asin."""
        conn = sqlite3.connect(clean_periodicals)
        # Insert two parents
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent 1", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B003333333", "Parent 2", "podcast", None),
        )
        # Insert episodes for both
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Episode P1", "podcast", "B001111111"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B004444444", "Episode P2", "podcast", "B003333333"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals?parent_asin=B001111111")

        data = response.get_json()
        assert len(data["periodicals"]) == 1
        assert data["periodicals"][0]["asin"] == "B002222222"


class TestListParents:
    """Test the list_parents route."""

    def test_returns_parents_with_episode_counts(self, flask_app, clean_periodicals):
        """Test returns parent items with their episode counts."""
        conn = sqlite3.connect(clean_periodicals)
        # Insert parent
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent Podcast", "podcast", None),
        )
        # Insert 3 episodes
        for i in range(3):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
                (f"B00{i + 2:07d}", f"Episode {i + 1}", "podcast", "B001111111"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/parents")

        assert response.status_code == 200
        data = response.get_json()
        assert len(data["parents"]) == 1
        assert data["parents"][0]["asin"] == "B001111111"
        assert data["parents"][0]["episode_count"] == 3

    def test_excludes_episodes_from_parents_list(self, flask_app, clean_periodicals):
        """Test episodes are not included in parents list."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Episode", "podcast", "B001111111"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/parents")

        data = response.get_json()
        assert len(data["parents"]) == 1
        assert data["parents"][0]["title"] == "Parent"

    def test_filter_parents_by_category(self, flask_app, clean_periodicals):
        """Test filtering parents by category."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Podcast Parent", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Meditation Parent", "meditation", None),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/parents?category=meditation")

        data = response.get_json()
        assert len(data["parents"]) == 1
        assert data["parents"][0]["category"] == "meditation"

    def test_sort_parents_by_episode_count(self, flask_app, clean_periodicals):
        """Test sorting parents by episode count."""
        conn = sqlite3.connect(clean_periodicals)
        # Parent with 2 episodes
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Few Episodes", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "EP1", "podcast", "B001111111"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B003333333", "EP2", "podcast", "B001111111"),
        )
        # Parent with 5 episodes
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B004444444", "Many Episodes", "podcast", None),
        )
        for i in range(5):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
                (f"B00{i + 5:07d}", f"EP{i + 1}", "podcast", "B004444444"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/parents?sort=episode_count")

        data = response.get_json()
        assert data["parents"][0]["title"] == "Many Episodes"
        assert data["parents"][0]["episode_count"] == 5
        assert data["parents"][1]["title"] == "Few Episodes"
        assert data["parents"][1]["episode_count"] == 2


class TestListEpisodes:
    """Test the list_episodes route."""

    def test_returns_episodes_for_parent(self, flask_app, clean_periodicals):
        """Test returns all episodes for a parent ASIN."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent Podcast", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin, release_date) VALUES (?, ?, ?, ?, ?)""",
            ("B002222222", "Episode 1", "podcast", "B001111111", "2024-01-01"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin, release_date) VALUES (?, ?, ?, ?, ?)""",
            ("B003333333", "Episode 2", "podcast", "B001111111", "2024-01-02"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B001111111/episodes")

        assert response.status_code == 200
        data = response.get_json()
        assert data["parent_asin"] == "B001111111"
        assert data["parent_title"] == "Parent Podcast"
        assert data["total"] == 2
        assert len(data["episodes"]) == 2

    def test_episodes_sorted_by_release_date(self, flask_app, clean_periodicals):
        """Test episodes are sorted by release date (newest first)."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin, release_date) VALUES (?, ?, ?, ?, ?)""",
            ("B002222222", "Old Episode", "podcast", "B001111111", "2024-01-01"),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin, release_date) VALUES (?, ?, ?, ?, ?)""",
            ("B003333333", "New Episode", "podcast", "B001111111", "2024-02-01"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B001111111/episodes")

        data = response.get_json()
        assert data["episodes"][0]["title"] == "New Episode"
        assert data["episodes"][1]["title"] == "Old Episode"

    def test_episodes_pagination(self, flask_app, clean_periodicals):
        """Test pagination for episodes list."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        for i in range(10):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
                (f"B00{i + 2:07d}", f"Episode {i + 1}", "podcast", "B001111111"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get(
                "/api/v1/periodicals/B001111111/episodes?page=2&per_page=3"
            )

        data = response.get_json()
        assert len(data["episodes"]) == 3
        assert data["page"] == 2
        assert data["total"] == 10
        assert data["total_pages"] == 4

    def test_returns_404_for_episode_asin(self, flask_app, clean_periodicals):
        """Test returns 404 when ASIN is an episode, not a parent."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Episode", "podcast", "B001111111"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B002222222/episodes")

        assert response.status_code == 404

    def test_returns_404_for_nonexistent_parent(self, flask_app, clean_periodicals):
        """Test returns 404 for nonexistent parent ASIN."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B999999999/episodes")

        assert response.status_code == 404

    def test_returns_400_for_invalid_asin(self, flask_app):
        """Test returns 400 for invalid ASIN format."""
        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/invalid/episodes")

        assert response.status_code == 400


class TestPeriodicalDetailsWithEpisodeCount:
    """Test periodical_details includes episode_count for parents."""

    def test_parent_includes_episode_count(self, flask_app, clean_periodicals):
        """Test parent item response includes episode_count."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        for i in range(5):
            conn.execute(
                """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
                (f"B00{i + 2:07d}", f"Episode {i + 1}", "podcast", "B001111111"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B001111111")

        data = response.get_json()
        assert "episode_count" in data
        assert data["episode_count"] == 5

    def test_episode_does_not_include_episode_count(self, flask_app, clean_periodicals):
        """Test episode item response does not include episode_count."""
        conn = sqlite3.connect(clean_periodicals)
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B001111111", "Parent", "podcast", None),
        )
        conn.execute(
            """INSERT INTO periodicals (asin, title, category, parent_asin) VALUES (?, ?, ?, ?)""",
            ("B002222222", "Episode", "podcast", "B001111111"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/v1/periodicals/B002222222")

        data = response.get_json()
        assert "episode_count" not in data
