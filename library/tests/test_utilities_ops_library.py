"""
Tests for library content management operations.

Tests the library sub-module of utilities_ops package:
- add-new audiobooks
- rescan library
- reimport database
"""

from unittest.mock import MagicMock, patch


class TestAddNewAudiobooks:
    """Test the add_new_audiobooks_endpoint."""

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_starts_add_operation(self, mock_get_tracker, flask_app):
        """Test starts add operation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-new-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/add-new",
                json={"calculate_hashes": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "operation_id" in data
        assert data["operation_id"] == "add-new-123"

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when add operation already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-op"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/add-new", json={})

        assert response.status_code == 409
        data = response.get_json()
        assert data["success"] is False
        assert "already in progress" in data["error"]
        assert data["operation_id"] == "existing-op"

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test add_new operation uses 'add_new' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/add-new", json={})

        mock_tracker.is_operation_running.assert_called_with("add_new")
        mock_tracker.create_operation.assert_called_with(
            "add_new", "Adding new audiobooks to database"
        )


class TestAddNewCalculateHashesOption:
    """Test the calculate_hashes option for add-new endpoint."""

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_accepts_calculate_hashes_true(self, mock_get_tracker, flask_app):
        """Test accepts calculate_hashes=True."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/add-new",
                json={"calculate_hashes": True},
            )

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_accepts_calculate_hashes_false(self, mock_get_tracker, flask_app):
        """Test accepts calculate_hashes=False."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-456"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/add-new",
                json={"calculate_hashes": False},
            )

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_defaults_calculate_hashes_to_true(self, mock_get_tracker, flask_app):
        """Test calculate_hashes defaults to True when not provided."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-789"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/add-new", json={})

        assert response.status_code == 200


class TestRescanLibraryAsync:
    """Test the rescan_library_async endpoint."""

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_starts_rescan_operation(self, mock_get_tracker, flask_app):
        """Test starts rescan operation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "rescan-123"
        assert "Rescan started" in data["message"]

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when rescan already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-rescan"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rescan-async")

        assert response.status_code == 409
        data = response.get_json()
        assert "Rescan already in progress" in data["error"]

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test rescan operation uses 'rescan' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        mock_tracker.is_operation_running.assert_called_with("rescan")
        mock_tracker.create_operation.assert_called_with(
            "rescan", "Scanning audiobook library"
        )


class TestReimportDatabaseAsync:
    """Test the reimport_database_async endpoint."""

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_starts_reimport_operation(self, mock_get_tracker, flask_app):
        """Test starts reimport operation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimport-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "reimport-123"

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when reimport already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-reimport"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/reimport-async")

        assert response.status_code == 409

    @patch("backend.api_modular.utilities_ops.library.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test reimport operation uses 'reimport' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        mock_tracker.is_operation_running.assert_called_with("reimport")


class TestEndpointMethodConstraints:
    """Test that library endpoints only respond to correct HTTP methods."""

    def test_add_new_only_post(self, flask_app):
        """Test add-new only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/add-new")
        assert response.status_code == 405

    def test_rescan_only_post(self, flask_app):
        """Test rescan only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/rescan-async")
        assert response.status_code == 405

    def test_reimport_only_post(self, flask_app):
        """Test reimport only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/reimport-async")
        assert response.status_code == 405
