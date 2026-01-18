"""
Tests for operation status tracking endpoints.

Tests the status sub-module of utilities_ops package.
"""

from unittest.mock import MagicMock, patch


class TestGetOperationStatus:
    """Test the get_operation_status endpoint."""

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_returns_status_for_existing_operation(self, mock_get_tracker, flask_app):
        """Test returns status when operation exists."""
        mock_tracker = MagicMock()
        mock_tracker.get_status.return_value = {
            "operation_id": "test-123",
            "status": "running",
            "progress": 50,
            "message": "Processing...",
        }
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.get("/api/operations/status/test-123")

        assert response.status_code == 200
        data = response.get_json()
        assert data["operation_id"] == "test-123"
        assert data["status"] == "running"
        assert data["progress"] == 50

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_returns_404_for_unknown_operation(self, mock_get_tracker, flask_app):
        """Test returns 404 when operation not found."""
        mock_tracker = MagicMock()
        mock_tracker.get_status.return_value = None
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.get("/api/operations/status/nonexistent")

        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data
        assert "not found" in data["error"]


class TestGetActiveOperations:
    """Test the get_active_operations endpoint."""

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_returns_active_operations(self, mock_get_tracker, flask_app):
        """Test returns list of active operations."""
        mock_tracker = MagicMock()
        mock_tracker.get_active_operations.return_value = [
            {"operation_id": "op-1", "type": "rescan", "progress": 25},
            {"operation_id": "op-2", "type": "hash", "progress": 75},
        ]
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.get("/api/operations/active")

        assert response.status_code == 200
        data = response.get_json()
        assert "operations" in data
        assert data["count"] == 2
        assert len(data["operations"]) == 2

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_returns_empty_when_no_active(self, mock_get_tracker, flask_app):
        """Test returns empty list when no active operations."""
        mock_tracker = MagicMock()
        mock_tracker.get_active_operations.return_value = []
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.get("/api/operations/active")

        assert response.status_code == 200
        data = response.get_json()
        assert data["operations"] == []
        assert data["count"] == 0


class TestGetAllOperations:
    """Test the get_all_operations endpoint."""

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_returns_all_operations(self, mock_get_tracker, flask_app):
        """Test returns all tracked operations."""
        mock_tracker = MagicMock()
        mock_tracker.get_all_operations.return_value = [
            {"operation_id": "op-1", "status": "completed"},
            {"operation_id": "op-2", "status": "running"},
            {"operation_id": "op-3", "status": "failed"},
        ]
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.get("/api/operations/all")

        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 3


class TestCancelOperation:
    """Test the cancel_operation endpoint."""

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_cancels_existing_operation(self, mock_get_tracker, flask_app):
        """Test successfully cancels an operation."""
        mock_tracker = MagicMock()
        mock_tracker.cancel_operation.return_value = True
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/operations/cancel/test-123")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "cancellation" in data["message"]

    @patch("backend.api_modular.utilities_ops.status.get_tracker")
    def test_returns_404_for_unknown_operation(self, mock_get_tracker, flask_app):
        """Test returns 404 when operation not found."""
        mock_tracker = MagicMock()
        mock_tracker.cancel_operation.return_value = False
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/operations/cancel/nonexistent")

        assert response.status_code == 404


class TestEndpointMethodConstraints:
    """Test that status endpoints only respond to correct HTTP methods."""

    def test_operation_status_only_get(self, flask_app):
        """Test operation status only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/operations/status/test-id")
        assert response.status_code == 405

    def test_active_operations_only_get(self, flask_app):
        """Test active operations only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/operations/active")
        assert response.status_code == 405

    def test_all_operations_only_get(self, flask_app):
        """Test all operations only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/operations/all")
        assert response.status_code == 405

    def test_cancel_operation_only_post(self, flask_app):
        """Test cancel operation only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/operations/cancel/test-id")
        assert response.status_code == 405
