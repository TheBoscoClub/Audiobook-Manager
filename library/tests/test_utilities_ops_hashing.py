"""
Tests for hash and checksum generation operations.

Tests the hashing sub-module of utilities_ops package:
- generate-hashes-async (SHA-256)
- generate-checksums-async (MD5)
"""

from unittest.mock import MagicMock, patch


class TestGenerateHashesAsync:
    """Test the generate_hashes_async endpoint."""

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_starts_hash_generation(self, mock_get_tracker, flask_app):
        """Test starts hash generation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "hash-123"

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when hash generation already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-hash"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes-async")

        assert response.status_code == 409
        data = response.get_json()
        assert "Hash generation already in progress" in data["error"]

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test hash operation uses 'hash' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        mock_tracker.is_operation_running.assert_called_with("hash")


class TestGenerateChecksumsAsync:
    """Test the generate_checksums_async endpoint."""

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_starts_checksum_generation(self, mock_get_tracker, flask_app):
        """Test starts checksum generation successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "checksum-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-checksums-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "checksum-123"

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_returns_409_when_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when checksum generation already in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-checksum"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-checksums-async")

        assert response.status_code == 409

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_uses_correct_operation_type(self, mock_get_tracker, flask_app):
        """Test checksum operation uses 'checksum' type."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "op-id"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-checksums-async")

        mock_tracker.is_operation_running.assert_called_with("checksum")


class TestEndpointMethodConstraints:
    """Test that hashing endpoints only respond to correct HTTP methods."""

    def test_generate_hashes_only_post(self, flask_app):
        """Test generate-hashes only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/generate-hashes-async")
        assert response.status_code == 405

    def test_generate_checksums_only_post(self, flask_app):
        """Test generate-checksums only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/generate-checksums-async")
        assert response.status_code == 405
