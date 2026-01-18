"""
Tests for position sync API module.

This module provides bidirectional playback position synchronization
between the local audiobook library and Audible cloud.
"""

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestRunAsync:
    """Test the run_async utility function."""

    def test_runs_coroutine(self):
        """Test runs async coroutine and returns result."""
        from backend.api_modular.position_sync import run_async

        async def sample_coro():
            return "result"

        result = run_async(sample_coro())
        assert result == "result"

    def test_handles_exception(self):
        """Test propagates exceptions from coroutine."""
        from backend.api_modular.position_sync import run_async

        async def failing_coro():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_async(failing_coro())


class TestGetAudibleClient:
    """Test the get_audible_client async function."""

    def test_raises_when_audible_unavailable(self):
        """Test raises when Audible library not available."""
        from backend.api_modular import position_sync
        from backend.api_modular.position_sync import run_async

        original_available = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = False
        position_sync.AUDIBLE_IMPORT_ERROR = "Test import error"

        try:
            with pytest.raises(RuntimeError, match="not available"):
                run_async(position_sync.get_audible_client())
        finally:
            position_sync.AUDIBLE_AVAILABLE = original_available

    def test_raises_when_auth_file_missing(self, temp_dir):
        """Test raises when auth file doesn't exist."""
        from backend.api_modular import position_sync
        from backend.api_modular.position_sync import run_async

        original_available = position_sync.AUDIBLE_AVAILABLE
        original_auth = position_sync.AUTH_FILE
        position_sync.AUDIBLE_AVAILABLE = True
        position_sync.AUTH_FILE = temp_dir / "nonexistent.json"

        try:
            with pytest.raises(RuntimeError, match="not found"):
                run_async(position_sync.get_audible_client())
        finally:
            position_sync.AUDIBLE_AVAILABLE = original_available
            position_sync.AUTH_FILE = original_auth


class TestFetchAudiblePosition:
    """Test the fetch_audible_position async function."""

    def test_returns_position_data(self):
        """Test returns position data for valid ASIN."""
        from backend.api_modular.position_sync import (fetch_audible_position,
                                                       run_async)

        mock_client = AsyncMock()
        mock_client.get.return_value = {
            "asin_last_position_heard_annots": [
                {
                    "asin": "B12345",
                    "last_position_heard": {
                        "position_ms": 5000000,
                        "last_updated": "2024-01-15T10:30:00Z",
                        "status": "InProgress",
                    },
                }
            ]
        }

        result = run_async(fetch_audible_position(mock_client, "B12345"))

        assert result["asin"] == "B12345"
        assert result["position_ms"] == 5000000
        assert result["status"] == "InProgress"

    def test_returns_not_found_for_missing_asin(self):
        """Test returns NotFound status when ASIN not in response."""
        from backend.api_modular.position_sync import (fetch_audible_position,
                                                       run_async)

        mock_client = AsyncMock()
        mock_client.get.return_value = {"asin_last_position_heard_annots": []}

        result = run_async(fetch_audible_position(mock_client, "B99999"))

        assert result["asin"] == "B99999"
        assert result["position_ms"] is None
        assert result["status"] == "NotFound"

    def test_returns_error_on_exception(self):
        """Test returns error dict on API exception."""
        from backend.api_modular.position_sync import (fetch_audible_position,
                                                       run_async)

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("API timeout")

        result = run_async(fetch_audible_position(mock_client, "B12345"))

        assert result["asin"] == "B12345"
        assert "error" in result
        assert "API timeout" in result["error"]


class TestFetchAudiblePositionsBatch:
    """Test the fetch_audible_positions_batch async function."""

    def test_returns_positions_for_multiple_asins(self):
        """Test returns positions for multiple ASINs."""
        from backend.api_modular.position_sync import (
            fetch_audible_positions_batch, run_async)

        mock_client = AsyncMock()
        mock_client.get.return_value = {
            "asin_last_position_heard_annots": [
                {
                    "asin": "B11111",
                    "last_position_heard": {
                        "position_ms": 1000000,
                        "status": "InProgress",
                    },
                },
                {
                    "asin": "B22222",
                    "last_position_heard": {
                        "position_ms": 2000000,
                        "status": "Complete",
                    },
                },
            ]
        }

        result = run_async(
            fetch_audible_positions_batch(mock_client, ["B11111", "B22222"])
        )

        assert "B11111" in result
        assert result["B11111"]["position_ms"] == 1000000
        assert "B22222" in result
        assert result["B22222"]["position_ms"] == 2000000

    def test_marks_missing_asins_as_not_found(self):
        """Test marks ASINs not in response as NotFound."""
        from backend.api_modular.position_sync import (
            fetch_audible_positions_batch, run_async)

        mock_client = AsyncMock()
        mock_client.get.return_value = {
            "asin_last_position_heard_annots": [
                {"asin": "B11111", "last_position_heard": {"position_ms": 1000000}},
            ]
        }

        result = run_async(
            fetch_audible_positions_batch(mock_client, ["B11111", "B99999"])
        )

        assert result["B11111"]["position_ms"] == 1000000
        assert result["B99999"]["status"] == "NotFound"

    def test_returns_error_on_chunk_failure(self):
        """Test returns error when any chunk fails."""
        from backend.api_modular.position_sync import (
            fetch_audible_positions_batch, run_async)

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("CloudFront error")

        result = run_async(fetch_audible_positions_batch(mock_client, ["B11111"]))

        assert "error" in result
        assert "Chunk 1 failed" in result["error"]


class TestPushAudiblePosition:
    """Test the push_audible_position async function."""

    def test_pushes_position_successfully(self):
        """Test successfully pushes position to Audible."""
        from backend.api_modular.position_sync import (push_audible_position,
                                                       run_async)

        mock_client = AsyncMock()
        mock_client.post.return_value = {"content_license": {"acr": "test-acr-123"}}
        mock_client.put.return_value = {}

        result = run_async(push_audible_position(mock_client, "B12345", 5000000))

        assert result["success"] is True
        assert result["asin"] == "B12345"
        assert result["position_ms"] == 5000000

    def test_returns_error_when_no_acr(self):
        """Test returns error when ACR not obtained."""
        from backend.api_modular.position_sync import (push_audible_position,
                                                       run_async)

        mock_client = AsyncMock()
        mock_client.post.return_value = {"content_license": {}}  # No ACR

        result = run_async(push_audible_position(mock_client, "B12345", 5000000))

        assert result["success"] is False
        assert "ACR" in result["error"]

    def test_returns_error_on_exception(self):
        """Test returns error on API exception."""
        from backend.api_modular.position_sync import (push_audible_position,
                                                       run_async)

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("License request failed")

        result = run_async(push_audible_position(mock_client, "B12345", 5000000))

        assert result["success"] is False
        assert "error" in result


class TestPositionStatusRoute:
    """Test the /api/position/status endpoint."""

    def test_returns_status(self, flask_app):
        """Test returns position sync status."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/status")

        assert response.status_code == 200
        data = response.get_json()
        assert "audible_available" in data
        assert "auth_file_exists" in data


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
            INSERT INTO audiobooks (
                id, title, author, asin, duration_hours, playback_position_ms,
                playback_position_updated, audible_position_ms, file_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9001,
                "Test Position Book",
                "Test Author",
                "B12345",
                10.0,
                5000000,
                "2024-01-15",
                4500000,
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
        assert data["syncable"] is True

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
            INSERT INTO audiobooks (id, title, author, duration_hours, playback_position_ms, file_path)
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
            "INSERT INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
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


class TestSyncPositionRoute:
    """Test the POST /api/position/sync/<id> endpoint."""

    def test_returns_503_when_audible_unavailable(self, flask_app):
        """Test returns 503 when Audible not available."""
        from backend.api_modular import position_sync

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = False

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync/1")

            assert response.status_code == 503
        finally:
            position_sync.AUDIBLE_AVAILABLE = original

    def test_returns_404_for_missing_audiobook(self, flask_app):
        """Test returns 404 for non-existent audiobook."""
        from backend.api_modular import position_sync

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync/99999")

            assert response.status_code == 404
        finally:
            position_sync.AUDIBLE_AVAILABLE = original

    def test_returns_400_for_book_without_asin(self, flask_app, session_temp_dir):
        """Test returns 400 for book without ASIN."""
        from backend.api_modular import position_sync

        # Insert book without ASIN (but with required fields)
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
            (9004, "No ASIN Book", "Author", 5.0, "/test/noasin.opus"),
        )
        conn.commit()
        conn.close()

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync/9004")

            assert response.status_code == 400
            assert "no ASIN" in response.get_json()["error"]
        finally:
            position_sync.AUDIBLE_AVAILABLE = original


class TestSyncAllPositionsRoute:
    """Test the POST /api/position/sync-all endpoint."""

    def test_returns_503_when_audible_unavailable(self, flask_app):
        """Test returns 503 when Audible not available."""
        from backend.api_modular import position_sync

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = False

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync-all")

            assert response.status_code == 503
        finally:
            position_sync.AUDIBLE_AVAILABLE = original

    def test_returns_message_when_no_syncable_books(self, flask_app, session_temp_dir):
        """Test returns message when no books with ASINs."""
        from backend.api_modular import position_sync

        # Clear any existing books with ASINs
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE audiobooks SET asin = NULL")
        conn.commit()
        conn.close()

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync-all")

            data = response.get_json()
            assert "synced" in data or "message" in data
        finally:
            position_sync.AUDIBLE_AVAILABLE = original


class TestListSyncableRoute:
    """Test the GET /api/position/syncable endpoint."""

    def test_returns_syncable_books(self, flask_app, session_temp_dir):
        """Test returns list of books with ASINs."""
        # Insert books with and without ASINs (include required fields)
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (id, title, author, asin, duration_hours, playback_position_ms, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9010,
                "Syncable Book",
                "Author Name",
                "B55555",
                6.0,
                2000000,
                "/test/syncable.opus",
            ),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/syncable")

        assert response.status_code == 200
        data = response.get_json()
        assert "total" in data
        assert "books" in data
        # Should include our syncable book
        asins = [b["asin"] for b in data["books"]]
        assert "B55555" in asins


class TestPositionHistoryRoute:
    """Test the GET /api/position/history/<id> endpoint."""

    def test_returns_history(self, flask_app, session_temp_dir):
        """Test returns position history for audiobook."""
        # Insert audiobook and history records (include required fields)
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
            (9020, "History Book", "Author", 5.0, "/test/history.opus"),
        )
        cursor.execute(
            """
            INSERT INTO playback_history (audiobook_id, position_ms, source)
            VALUES (?, ?, ?), (?, ?, ?)
            """,
            (9020, 1000000, "local", 9020, 2000000, "sync"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/history/9020")

        assert response.status_code == 200
        data = response.get_json()
        assert data["audiobook_id"] == 9020
        assert len(data["history"]) == 2

    def test_respects_limit_parameter(self, flask_app, session_temp_dir):
        """Test respects limit query parameter."""
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
            (9021, "Limit Test Book", "Author", 3.0, "/test/limit.opus"),
        )
        # Insert multiple history records
        for i in range(10):
            cursor.execute(
                "INSERT INTO playback_history (audiobook_id, position_ms, source) VALUES (?, ?, ?)",
                (9021, i * 100000, "local"),
            )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/history/9021?limit=3")

        data = response.get_json()
        assert len(data["history"]) == 3


class TestBatchChunking:
    """Test batch chunking logic for large requests."""

    def test_processes_in_chunks(self):
        """Test processes large ASIN lists in chunks."""
        from backend.api_modular.position_sync import (
            fetch_audible_positions_batch, run_async)

        # Create 50 ASINs (should result in 2 chunks with BATCH_CHUNK_SIZE=25)
        asins = [f"B{i:05d}" for i in range(50)]

        call_count = [0]  # Use list to allow modification in nested function

        async def mock_get(*args, **kwargs):
            call_count[0] += 1
            # Return positions for whatever ASINs were requested
            requested_asins = kwargs.get("params", {}).get("asins", "").split(",")
            return {
                "asin_last_position_heard_annots": [
                    {"asin": asin, "last_position_heard": {"position_ms": 1000}}
                    for asin in requested_asins
                    if asin
                ]
            }

        mock_client = MagicMock()
        mock_client.get = mock_get

        result = run_async(fetch_audible_positions_batch(mock_client, asins))

        # Should have made 2 API calls (50 / 25 = 2)
        assert call_count[0] == 2
        # All ASINs should have results
        assert len(result) == 50


class TestSyncPositionWithMockedAudible:
    """Test sync position with mocked Audible client."""

    @patch("backend.api_modular.position_sync.run_async")
    def test_sync_pulls_from_audible_when_ahead(
        self, mock_run_async, flask_app, session_temp_dir
    ):
        """Test sync pulls position from Audible when Audible is ahead."""
        from backend.api_modular import position_sync

        # Insert book with local position behind Audible
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (id, title, author, asin, duration_hours, playback_position_ms, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9050,
                "Sync Pull Book",
                "Author",
                "B99001",
                8.0,
                1000000,
                "/test/syncpull.opus",
            ),
        )
        conn.commit()
        conn.close()

        # Mock Audible returning higher position
        mock_run_async.return_value = (
            {
                "audiobook_id": 9050,
                "title": "Sync Pull Book",
                "asin": "B99001",
                "local_position_ms": 1000000,
                "local_position_human": "16m 40s",
                "audible_position_ms": 5000000,
                "audible_position_human": "1h 23m 20s",
                "action": "pulled_from_audible",
                "final_position_ms": 5000000,
                "final_position_human": "1h 23m 20s",
            },
            5000000,  # audible_pos
            "2024-01-15T12:00:00",  # now
        )

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync/9050")

            assert response.status_code == 200
            data = response.get_json()
            assert data["action"] == "pulled_from_audible"
        finally:
            position_sync.AUDIBLE_AVAILABLE = original

    @patch("backend.api_modular.position_sync.run_async")
    def test_sync_handles_error_from_audible(
        self, mock_run_async, flask_app, session_temp_dir
    ):
        """Test sync handles error returned from Audible."""
        from backend.api_modular import position_sync

        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (id, title, author, asin, duration_hours, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (9051, "Sync Error Book", "Author", "B99002", 5.0, "/test/syncerr.opus"),
        )
        conn.commit()
        conn.close()

        # Mock Audible returning error
        mock_run_async.return_value = {"error": "API rate limited"}

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync/9051")

            assert response.status_code == 500
        finally:
            position_sync.AUDIBLE_AVAILABLE = original


class TestSyncAllWithMockedAudible:
    """Test sync all positions with mocked Audible client."""

    @patch("backend.api_modular.position_sync.run_async")
    def test_sync_all_processes_multiple_books(
        self, mock_run_async, flask_app, session_temp_dir
    ):
        """Test sync all processes multiple syncable books."""
        from backend.api_modular import position_sync

        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Insert multiple books with ASINs
        cursor.execute(
            """
            INSERT INTO audiobooks (id, title, author, asin, duration_hours, playback_position_ms, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9060,
                "Batch Book 1",
                "Author",
                "B99010",
                5.0,
                1000000,
                "/test/batch1.opus",
                9061,
                "Batch Book 2",
                "Author",
                "B99011",
                6.0,
                2000000,
                "/test/batch2.opus",
            ),
        )
        conn.commit()
        conn.close()

        # Mock batch sync returning success
        mock_run_async.return_value = (
            [
                {
                    "audiobook_id": 9060,
                    "asin": "B99010",
                    "action": "already_synced",
                    "final_position_ms": 1000000,
                    "audible_position_ms": 1000000,
                },
                {
                    "audiobook_id": 9061,
                    "asin": "B99011",
                    "action": "pulled_from_audible",
                    "final_position_ms": 3000000,
                    "audible_position_ms": 3000000,
                },
            ],
            {"B99010": {"position_ms": 1000000}, "B99011": {"position_ms": 3000000}},
        )

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync-all")

            assert response.status_code == 200
            data = response.get_json()
            assert data["total"] == 2
        finally:
            position_sync.AUDIBLE_AVAILABLE = original

    @patch("backend.api_modular.position_sync.run_async")
    def test_sync_all_handles_error(self, mock_run_async, flask_app, session_temp_dir):
        """Test sync all handles Audible errors."""
        from backend.api_modular import position_sync

        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (id, title, author, asin, duration_hours, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (9062, "Batch Error Book", "Author", "B99012", 4.0, "/test/batcherr.opus"),
        )
        conn.commit()
        conn.close()

        # Mock error response
        mock_run_async.return_value = {"error": "Batch request failed"}

        original = position_sync.AUDIBLE_AVAILABLE
        position_sync.AUDIBLE_AVAILABLE = True

        try:
            with flask_app.test_client() as client:
                response = client.post("/api/position/sync-all")

            assert response.status_code == 500
        finally:
            position_sync.AUDIBLE_AVAILABLE = original


class TestPercentageCalculation:
    """Test percentage completion calculations."""

    def test_calculates_percent_correctly(self, flask_app, session_temp_dir):
        """Test correctly calculates completion percentage."""
        db_path = session_temp_dir / "test_audiobooks.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audiobooks (id, title, author, asin, duration_hours, playback_position_ms, file_path)
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
            "INSERT INTO audiobooks (id, title, author, duration_hours, file_path) VALUES (?, ?, ?, ?, ?)",
            (9031, "Zero Duration Book", "Author", 0, "/test/zerodur.opus"),
        )
        conn.commit()
        conn.close()

        with flask_app.test_client() as client:
            response = client.get("/api/position/9031")

        data = response.get_json()
        assert data["percent_complete"] == 0
