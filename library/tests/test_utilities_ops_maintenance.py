"""
Tests for system maintenance operations.

Tests the maintenance sub-module of utilities_ops package:
- rebuild-queue-async
- cleanup-indexes-async
- populate-sort-fields-async
- find-source-duplicates-async
"""

from unittest.mock import MagicMock, patch


class TestRebuildQueueAsync:
    """Test the rebuild_queue_async endpoint."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_rebuild_operation(self, mock_get_tracker, flask_app):
        """Test starts queue rebuild operation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rebuild-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rebuild-queue-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "rebuild-123"

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when queue rebuild already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-rebuild"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rebuild-queue-async")

        assert response.status_code == 409

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test rebuild queue operation uses 'rebuild_queue' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        mock_tracker.is_operation_running.assert_called_with("rebuild_queue")


class TestCleanupIndexesAsync:
    """Test the cleanup_indexes_async endpoint."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_cleanup_operation_dry_run(self, mock_get_tracker, flask_app):
        """Test starts cleanup operation in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cleanup-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/cleanup-indexes-async",
                json={"dry_run": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_cleanup_operation_execute(self, mock_get_tracker, flask_app):
        """Test starts cleanup operation in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cleanup-456"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/cleanup-indexes-async",
                json={"dry_run": False},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "dry run" not in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when cleanup already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-cleanup"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/cleanup-indexes-async", json={})

        assert response.status_code == 409

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test cleanup defaults to dry_run=True when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cleanup-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/cleanup-indexes-async", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]


class TestPopulateSortFieldsAsync:
    """Test the populate_sort_fields_async endpoint."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_sort_fields_dry_run(self, mock_get_tracker, flask_app):
        """Test starts sort field population in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sort-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/populate-sort-fields-async",
                json={"dry_run": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_sort_fields_execute(self, mock_get_tracker, flask_app):
        """Test starts sort field population in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sort-456"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/populate-sort-fields-async",
                json={"dry_run": False},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when sort field population already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-sort"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/populate-sort-fields-async", json={})

        assert response.status_code == 409

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test sort fields defaults to dry_run=True when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sort-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/populate-sort-fields-async", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]


class TestFindSourceDuplicatesAsync:
    """Test the find_source_duplicates_async endpoint."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_duplicate_scan_dry_run(self, mock_get_tracker, flask_app):
        """Test starts duplicate scan in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async",
                json={"dry_run": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_starts_duplicate_scan_execute(self, mock_get_tracker, flask_app):
        """Test starts duplicate scan in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-456"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async",
                json={"dry_run": False},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when duplicate scan already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-dup"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async", json={}
            )

        assert response.status_code == 409
        data = response.get_json()
        assert "Duplicate scan already in progress" in data["error"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test duplicates scan defaults to dry_run=True when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async", json={}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]


class TestEndpointMethodConstraints:
    """Test that maintenance endpoints only respond to correct HTTP methods."""

    def test_rebuild_queue_only_post(self, flask_app):
        """Test rebuild-queue only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/rebuild-queue-async")
        assert response.status_code == 405

    def test_cleanup_indexes_only_post(self, flask_app):
        """Test cleanup-indexes only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/cleanup-indexes-async")
        assert response.status_code == 405

    def test_sort_fields_only_post(self, flask_app):
        """Test populate-sort-fields only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/populate-sort-fields-async")
        assert response.status_code == 405

    def test_duplicates_only_post(self, flask_app):
        """Test find-source-duplicates only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/find-source-duplicates-async")
        assert response.status_code == 405
