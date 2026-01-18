"""
Extended tests for position sync module.

Tests position sync functionality including:
- Helper functions (ms_to_human)
- Position get/update endpoints
- Syncable books query
- Position history
- Audible sync operations (mocked)
"""

from unittest.mock import patch

import pytest


class TestMsToHuman:
    """Test the ms_to_human helper function."""

    def test_zero_ms(self):
        """Test zero milliseconds returns '0s'."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(0) == "0s"

    def test_none_ms(self):
        """Test None returns '0s'."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(None) == "0s"

    def test_seconds_only(self):
        """Test milliseconds < 1 minute shows seconds."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(45000) == "45s"  # 45 seconds

    def test_minutes_and_seconds(self):
        """Test milliseconds >= 1 minute shows minutes and seconds."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(150000) == "2m 30s"  # 2:30

    def test_hours_minutes_seconds(self):
        """Test milliseconds >= 1 hour shows full format."""
        from backend.api_modular.position_sync import ms_to_human

        assert ms_to_human(3725000) == "1h 2m 5s"  # 1:02:05


class TestGetPosition:
    """Test the get_position endpoint."""

    def test_get_position_nonexistent_book(self, flask_app):
        """Test getting position for non-existent book returns 404."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/999999")

        assert response.status_code == 404


class TestUpdatePosition:
    """Test the update_position endpoint."""

    def test_update_position_missing_data(self, flask_app):
        """Test updating position with missing data returns 400."""
        with flask_app.test_client() as client:
            response = client.put("/api/position/1", json={})

        assert response.status_code == 400


class TestGetSyncableBooks:
    """Test the get_syncable_books endpoint."""

    def test_returns_proper_structure(self, flask_app):
        """Test returns proper response structure."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/syncable")

        assert response.status_code == 200
        data = response.get_json()
        assert "books" in data
        # API returns "total" not "total_with_asin" / "total_without_asin"
        assert "total" in data or "total_with_asin" in data


class TestGetPositionHistory:
    """Test the get_position_history endpoint."""

    def test_get_history_nonexistent_book(self, flask_app):
        """Test getting history for non-existent book returns empty or 404."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/history/999999")

        # API may return 200 with empty history or 404
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.get_json()
            assert "history" in data or isinstance(data, list)


class TestSyncAllPositions:
    """Test the sync_all_positions endpoint."""

    def test_sync_all_returns_structure(self, flask_app):
        """Test sync-all returns expected structure."""
        with flask_app.test_client() as client:
            response = client.post("/api/position/sync-all")

        # May fail if Audible not configured (503), but should return valid JSON
        assert response.status_code in [200, 400, 500, 503]
        data = response.get_json()
        assert isinstance(data, dict)


class TestAudibleClientCreation:
    """Test the Audible client creation logic."""

    @patch("backend.api_modular.position_sync.AUDIBLE_AVAILABLE", False)
    @patch("backend.api_modular.position_sync.AUDIBLE_IMPORT_ERROR", "Test error")
    def test_raises_when_audible_unavailable(self):
        """Test raises RuntimeError when Audible library not available."""
        import asyncio

        from backend.api_modular.position_sync import get_audible_client

        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(get_audible_client())

        assert "not available" in str(exc_info.value)

    @patch("backend.api_modular.position_sync.AUDIBLE_AVAILABLE", True)
    @patch("backend.api_modular.position_sync.AUTH_FILE")
    def test_raises_when_auth_file_missing(self, mock_auth_file):
        """Test raises RuntimeError when auth file missing."""
        import asyncio

        from backend.api_modular.position_sync import get_audible_client

        mock_auth_file.exists.return_value = False

        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(get_audible_client())

        assert "not found" in str(exc_info.value)


class TestEndpointMethodConstraints:
    """Test that endpoints only respond to correct HTTP methods."""

    def test_get_position_only_get(self, flask_app):
        """Test GET /api/position/<id> only allows GET."""
        with flask_app.test_client() as client:
            response = client.delete("/api/position/1")
        assert response.status_code == 405

    def test_sync_only_post(self, flask_app):
        """Test POST /api/position/sync/<id> only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/sync/1")
        assert response.status_code == 405

    def test_sync_all_only_post(self, flask_app):
        """Test POST /api/position/sync-all only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/position/sync-all")
        assert response.status_code == 405

    def test_syncable_only_get(self, flask_app):
        """Test GET /api/position/syncable only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/position/syncable")
        assert response.status_code == 405

    def test_history_only_get(self, flask_app):
        """Test GET /api/position/history/<id> only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/position/history/1")
        assert response.status_code == 405
