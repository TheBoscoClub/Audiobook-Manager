"""
Extended tests for maintenance operations module.

Tests background thread functions and maintenance logic:
- rebuild-queue-async
- cleanup-indexes-async
- populate-sort-fields-async
- find-source-duplicates-async
"""

import subprocess
from unittest.mock import MagicMock, patch


class TestRebuildQueueBackgroundThread:
    """Test the background rebuild queue thread logic."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_successful_rebuild(self, mock_run, mock_get_tracker, flask_app):
        """Test successful queue rebuild completes operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rebuild-test-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Queue rebuilt successfully"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rebuild-queue-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_rebuild_failure(self, mock_run, mock_get_tracker, flask_app):
        """Test rebuild failure is tracked."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rebuild-fail-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Rebuild failed"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rebuild-queue-async")

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_rebuild_timeout(self, mock_run, mock_get_tracker, flask_app):
        """Test rebuild timeout is handled."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rebuild-timeout-123"
        mock_get_tracker.return_value = mock_tracker

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=300)

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rebuild-queue-async")

        assert response.status_code == 200


class TestCleanupIndexesBackgroundThread:
    """Test the background cleanup indexes thread logic."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_cleanup_dry_run(self, mock_run, mock_get_tracker, flask_app):
        """Test cleanup in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cleanup-dry-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Would clean 5 entries (dry run)"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/cleanup-indexes-async", json={"dry_run": True}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_cleanup_execute(self, mock_run, mock_get_tracker, flask_app):
        """Test cleanup in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cleanup-exec-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Cleaned 5 entries"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/cleanup-indexes-async", json={"dry_run": False}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" not in data["message"]


class TestPopulateSortFieldsBackgroundThread:
    """Test the background populate sort fields thread logic."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_sort_fields_dry_run(self, mock_run, mock_get_tracker, flask_app):
        """Test sort field population in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sort-dry-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Would update 100 records (dry run)"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/populate-sort-fields-async", json={"dry_run": True}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_sort_fields_execute(self, mock_run, mock_get_tracker, flask_app):
        """Test sort field population in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sort-exec-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Updated 100 records"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/populate-sort-fields-async", json={"dry_run": False}
            )

        assert response.status_code == 200


class TestFindSourceDuplicatesBackgroundThread:
    """Test the background find source duplicates thread logic."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_duplicates_dry_run(self, mock_run, mock_get_tracker, flask_app):
        """Test duplicate scan in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-dry-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Found 10 duplicate groups (dry run)"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async", json={"dry_run": True}
            )

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_duplicates_execute(self, mock_run, mock_get_tracker, flask_app):
        """Test duplicate scan in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-exec-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Processed 10 duplicate groups"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async", json={"dry_run": False}
            )

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_duplicates_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when duplicate scan already running."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-dup"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/find-source-duplicates-async", json={}
            )

        assert response.status_code == 409
        data = response.get_json()
        assert "already in progress" in data["error"]


class TestBackgroundThreadExceptionHandling:
    """Test exception handling in background threads."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    @patch("backend.api_modular.utilities_ops.maintenance.subprocess.run")
    def test_handles_generic_exception(self, mock_run, mock_get_tracker, flask_app):
        """Test background thread handles generic exceptions."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "exc-123"
        mock_get_tracker.return_value = mock_tracker

        mock_run.side_effect = Exception("Unexpected error")

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/rebuild-queue-async")

        # Endpoint should still succeed (thread starts)
        assert response.status_code == 200


class TestDefaultDryRunBehavior:
    """Test default dry_run=True behavior for safety."""

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_cleanup_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test cleanup defaults to dry run when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cleanup-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/cleanup-indexes-async", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_sort_fields_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test sort fields defaults to dry run when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sort-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/populate-sort-fields-async", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.maintenance.get_tracker")
    def test_duplicates_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test duplicate scan defaults to dry run when not specified."""
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
