"""
Tests for Audible integration operations.

Tests the audible sub-module of utilities_ops package:
- download-audiobooks-async
- sync-genres-async
- sync-narrators-async
- check-audible-prereqs
"""

from unittest.mock import MagicMock, patch


class TestDownloadAudiobooksAsync:
    """Test the download_audiobooks_async endpoint."""

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_starts_download_operation(self, mock_get_tracker, flask_app):
        """Test starts download operation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "download-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/download-audiobooks-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "download-123"

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when download already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-download"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/download-audiobooks-async")

        assert response.status_code == 409
        data = response.get_json()
        assert "Download already in progress" in data["error"]

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test download operation uses 'download' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        mock_tracker.is_operation_running.assert_called_with("download")


class TestSyncGenresAsync:
    """Test the sync_genres_async endpoint."""

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_starts_genre_sync_dry_run(self, mock_get_tracker, flask_app):
        """Test starts genre sync in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/sync-genres-async",
                json={"dry_run": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_starts_genre_sync_execute(self, mock_get_tracker, flask_app):
        """Test starts genre sync in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-456"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/sync-genres-async",
                json={"dry_run": False},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when genre sync already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-genre"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/sync-genres-async", json={})

        assert response.status_code == 409
        data = response.get_json()
        assert "Genre sync already in progress" in data["error"]

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test sync genres defaults to dry_run=True when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/sync-genres-async", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]


class TestSyncNarratorsAsync:
    """Test the sync_narrators_async endpoint."""

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_starts_narrator_sync_dry_run(self, mock_get_tracker, flask_app):
        """Test starts narrator sync in dry run mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narrator-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/sync-narrators-async",
                json={"dry_run": True},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "dry run" in data["message"]

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_starts_narrator_sync_execute(self, mock_get_tracker, flask_app):
        """Test starts narrator sync in execute mode."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narrator-456"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post(
                "/api/utilities/sync-narrators-async",
                json={"dry_run": False},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when narrator sync already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-narrator"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/sync-narrators-async", json={})

        assert response.status_code == 409

    @patch("backend.api_modular.utilities_ops.audible.get_tracker")
    def test_defaults_to_dry_run(self, mock_get_tracker, flask_app):
        """Test sync narrators defaults to dry_run=True when not specified."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narrator-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/sync-narrators-async", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert "dry run" in data["message"]


class TestCheckAudiblePrereqs:
    """Test the check_audible_prereqs endpoint."""

    @patch("backend.api_modular.utilities_ops.audible.os.path.isfile")
    @patch("backend.api_modular.utilities_ops.audible.os.environ.get")
    def test_returns_true_when_metadata_exists(
        self, mock_env_get, mock_isfile, flask_app
    ):
        """Test returns true when library_metadata.json exists."""
        mock_env_get.return_value = "/srv/audiobooks"
        mock_isfile.return_value = True

        with flask_app.test_client() as client:
            response = client.get("/api/utilities/check-audible-prereqs")

        assert response.status_code == 200
        data = response.get_json()
        assert data["library_metadata_exists"] is True
        assert data["library_metadata_path"] is not None

    @patch("backend.api_modular.utilities_ops.audible.os.path.isfile")
    @patch("backend.api_modular.utilities_ops.audible.os.environ.get")
    def test_returns_false_when_metadata_missing(
        self, mock_env_get, mock_isfile, flask_app
    ):
        """Test returns false when library_metadata.json missing."""
        mock_env_get.return_value = "/srv/audiobooks"
        mock_isfile.return_value = False

        with flask_app.test_client() as client:
            response = client.get("/api/utilities/check-audible-prereqs")

        assert response.status_code == 200
        data = response.get_json()
        assert data["library_metadata_exists"] is False
        assert data["library_metadata_path"] is None


class TestEndpointMethodConstraints:
    """Test that audible endpoints only respond to correct HTTP methods."""

    def test_download_only_post(self, flask_app):
        """Test download-audiobooks only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/download-audiobooks-async")
        assert response.status_code == 405

    def test_sync_genres_only_post(self, flask_app):
        """Test sync-genres only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/sync-genres-async")
        assert response.status_code == 405

    def test_sync_narrators_only_post(self, flask_app):
        """Test sync-narrators only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/sync-narrators-async")
        assert response.status_code == 405

    def test_check_prereqs_only_get(self, flask_app):
        """Test check-audible-prereqs only allows GET."""
        with flask_app.test_client() as client:
            response = client.post("/api/utilities/check-audible-prereqs")
        assert response.status_code == 405
