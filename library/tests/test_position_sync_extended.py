"""
Extended tests for position sync module.

Tests position tracking functionality including:
- Helper functions (ms_to_human)
- Position get/update endpoints
- Endpoint method constraints
"""


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


class TestEndpointMethodConstraints:
    """Test that endpoints only respond to correct HTTP methods."""

    def test_get_position_only_get(self, flask_app):
        """Test GET /api/position/<id> only allows GET and PUT."""
        with flask_app.test_client() as client:
            response = client.delete("/api/position/1")
        assert response.status_code == 405

    def test_status_only_get(self, flask_app):
        """Test GET /api/position/status only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/position/status")
        assert response.status_code == 405
