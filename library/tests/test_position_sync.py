"""
Tests for position sync API module.

Tests position tracking functionality including:
- Helper functions (ms_to_human)
- Database initialization
- Position get/update endpoints
- Percentage calculations
"""

import sqlite3

import pytest


class TestMsToHuman:
    """Test the ms_to_human utility function."""

    def test_zero_returns_zero_s(self):
        """Test zero milliseconds returns '0s'."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(0) == "0s"

    def test_none_returns_zero_s(self):
        """Test None returns '0s'."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(None) == "0s"

    def test_seconds_only(self):
        """Test seconds-only format."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(45000) == "45s"  # 45 seconds

    def test_minutes_and_seconds(self):
        """Test minutes and seconds format."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(125000) == "2m 5s"  # 2 minutes 5 seconds

    def test_hours_minutes_seconds(self):
        """Test hours, minutes, and seconds format."""
        from backend.api_modular.position_sync import ms_to_human

        # 2 hours 30 minutes 15 seconds = 9015 seconds = 9015000 ms
        assert ms_to_human(9015000) == "2h 30m 15s"


class TestGetDb:
    """Test the get_db function."""

    def test_raises_when_not_initialized(self):
        """Test raises RuntimeError when not initialized."""
        from backend.api_modular import position_sync

        # Save and clear the db path
        original = position_sync._db_path
        position_sync._db_path = None

        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                position_sync.get_db()
        finally:
            position_sync._db_path = original

    def test_returns_connection_when_initialized(self, temp_dir):
        """Test returns connection when properly initialized."""
        from backend.api_modular import position_sync
        from tests.conftest import init_test_database

        db_path = temp_dir / "test.db"
        init_test_database(db_path)

        original = position_sync._db_path
        position_sync._db_path = db_path

        try:
            conn = position_sync.get_db()
            assert conn is not None
            conn.close()
        finally:
            position_sync._db_path = original


class TestInitPositionRoutes:
    """Test the init_position_routes function."""

    def test_sets_db_path(self, temp_dir):
        """Test sets the module-level database path."""
        from backend.api_modular import position_sync

        db_path = temp_dir / "test.db"
        original = position_sync._db_path

        try:
            position_sync.init_position_routes(db_path)
            assert position_sync._db_path == db_path
        finally:
            position_sync._db_path = original


class TestPositionStatusRoute:
    """Test the /api/position/status endpoint."""

    def test_returns_status(self, flask_app):
        """Test returns position tracking status."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/status")

        assert response.status_code == 200
        data = response.get_json()
        assert "per_user" in data


class TestGetPositionRoute:
    """Test the GET /api/position/<id> endpoint."""

    def test_returns_position_for_audiobook(self, flask_app, session_temp_dir):
        """Test returns position data for existing audiobook."""
        # Insert test audiobook with all required fields
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO audiobooks (
                id, title, author, asin, duration_hours, playback_position_ms,
                playback_position_updated, file_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9001,
                "Test Position Book",
                "Test Author",
                "B12345",
                10.0,
                5000000,
                "2024-01-15",
                "/test/position_book.opus",
            ),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/9001")

        assert response.status_code == 200
        data = response.get_json()
        assert data["id"] == 9001
        assert data["title"] == "Test Position Book"
        assert data["local_position_ms"] == 5000000

    def test_returns_404_for_missing_audiobook(self, flask_app):
        """Test returns 404 for non-existent audiobook."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/99999")

        assert response.status_code == 404


class TestUpdatePositionRoute:
    """Test the PUT /api/position/<id> endpoint."""

    def test_updates_position(self, flask_app, session_temp_dir):
        """Test updates local playback position."""
        # Insert test audiobook with all required fields
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO audiobooks (id, title, author, duration_hours, playback_position_ms, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (9002, "Update Position Book", "Author", 8.0, 1000000, "/test/update.opus"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.put(
                "/api/position/9002",
                json={"position_ms": 3000000},
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["position_ms"] == 3000000

    def test_returns_400_without_position(self, flask_app, session_temp_dir):
        """Test returns 400 when position_ms not provided."""
        # Insert test audiobook with all required fields
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
            (9003, "No Position Book", "Author", 5.0, "/test/nopos.opus"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.put(
                "/api/position/9003",
                json={},
                content_type="application/json",
            )

        assert response.status_code == 400

    def test_returns_404_for_missing_audiobook(self, flask_app):
        """Test returns 404 for non-existent audiobook."""
        with flask_app.test_client() as client:
            response = client.put(
                "/api/position/99999",
                json={"position_ms": 1000000},
                content_type="application/json",
            )

        assert response.status_code == 404


class TestPercentageCalculation:
    """Test percentage completion calculations."""

    def test_calculates_percent_correctly(self, flask_app, session_temp_dir):
        """Test correctly calculates completion percentage."""
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO audiobooks (id, title, author, asin, duration_hours, playback_position_ms, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9030,
                "Percent Test",
                "Author",
                "B77777",
                10.0,
                18000000,
                "/test/percent.opus",
            ),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/9030")

        data = response.get_json()
        # 5 hours / 10 hours = 50%
        assert data["percent_complete"] == 50.0

    def test_handles_zero_duration(self, flask_app, session_temp_dir):
        """Test handles zero duration gracefully."""
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
            (9031, "Zero Duration Book", "Author", 0, "/test/zerodur.opus"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/9031")

        data = response.get_json()
        assert data["percent_complete"] == 0
