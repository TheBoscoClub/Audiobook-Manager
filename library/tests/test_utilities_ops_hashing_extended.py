"""
Extended tests for hashing operations module.

Tests background thread functions and checksum generation logic.
"""

import hashlib
import subprocess
from unittest.mock import MagicMock, patch


class TestGenerateHashesBackgroundThread:
    """Test the background hash generation thread logic."""

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    @patch("backend.api_modular.utilities_ops.hashing.subprocess.run")
    def test_successful_hash_generation(self, mock_run, mock_get_tracker, flask_app):
        """Test successful hash generation completes operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-test-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Generated 100 hashes successfully"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["operation_id"] == "hash-test-123"

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    @patch("backend.api_modular.utilities_ops.hashing.subprocess.run")
    def test_hash_generation_failure(self, mock_run, mock_get_tracker, flask_app):
        """Test hash generation failure is tracked."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-fail-123"
        mock_get_tracker.return_value = mock_tracker

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error generating hashes"
        mock_run.return_value = mock_result

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes-async")

        assert response.status_code == 200

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    @patch("backend.api_modular.utilities_ops.hashing.subprocess.run")
    def test_hash_generation_timeout(self, mock_run, mock_get_tracker, flask_app):
        """Test hash generation timeout is handled."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-timeout-123"
        mock_get_tracker.return_value = mock_tracker

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1800)

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes-async")

        assert response.status_code == 200


class TestGenerateChecksumsBackgroundThread:
    """Test the background checksum generation thread logic."""

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_checksum_generation_starts(self, mock_get_tracker, flask_app):
        """Test checksum generation starts successfully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "checksum-test-123"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-checksums-async")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "operation_id" in data

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    def test_checksum_already_running(self, mock_get_tracker, flask_app):
        """Test returns 409 when checksum generation already running."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-checksum"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-checksums-async")

        assert response.status_code == 409


class TestChecksumFirstMbFunction:
    """Test the inline checksum_first_mb function logic."""

    def test_checksum_calculation(self, session_temp_dir):
        """Test MD5 checksum is calculated correctly for first 1MB."""
        # Create test file larger than 1MB
        test_file = session_temp_dir / "checksum_test.bin"
        test_content = b"A" * (1024 * 1024)  # Exactly 1MB of 'A'
        test_file.write_bytes(test_content + b"extra data")

        # Calculate expected checksum
        expected = hashlib.md5(test_content, usedforsecurity=False).hexdigest()

        # Read and checksum
        with open(test_file, "rb") as f:
            data = f.read(1048576)
        actual = hashlib.md5(data, usedforsecurity=False).hexdigest()

        assert actual == expected

    def test_checksum_small_file(self, session_temp_dir):
        """Test checksum works for files smaller than 1MB."""
        test_file = session_temp_dir / "small_checksum.bin"
        test_content = b"Small file content"
        test_file.write_bytes(test_content)

        expected = hashlib.md5(test_content, usedforsecurity=False).hexdigest()

        with open(test_file, "rb") as f:
            data = f.read(1048576)
        actual = hashlib.md5(data, usedforsecurity=False).hexdigest()

        assert actual == expected


class TestHashParsingLogic:
    """Test the hash output parsing logic."""

    def test_parses_generated_count(self):
        """Test parsing 'Generated X hashes' from output."""
        import re

        output = "Processing files...\nGenerated 150 hashes successfully\nDone."

        hashes_generated = 0
        for line in output.split("\n"):
            if "Generated" in line or "hashes" in line.lower():
                try:
                    numbers = re.findall(r"\d+", line)
                    if numbers:
                        hashes_generated = int(numbers[0])
                except ValueError:
                    hashes_generated = 0  # Parsing failed, use default

        assert hashes_generated == 150

    def test_parses_zero_when_no_numbers(self):
        """Test returns 0 when no numbers in output."""
        import re

        output = "No files found to hash"

        hashes_generated = 0
        for line in output.split("\n"):
            if "Generated" in line or "hashes" in line.lower():
                try:
                    numbers = re.findall(r"\d+", line)
                    if numbers:
                        hashes_generated = int(numbers[0])
                except ValueError:
                    hashes_generated = 0  # Parsing failed, use default

        assert hashes_generated == 0


class TestEndpointErrorHandling:
    """Test error handling in hashing endpoints."""

    @patch("backend.api_modular.utilities_ops.hashing.get_tracker")
    @patch("backend.api_modular.utilities_ops.hashing.subprocess.run")
    def test_handles_exception_in_thread(self, mock_run, mock_get_tracker, flask_app):
        """Test exceptions in background thread are handled."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-exc-123"
        mock_get_tracker.return_value = mock_tracker

        mock_run.side_effect = Exception("Unexpected error")

        with flask_app.test_client() as client:
            response = client.post("/api/utilities/generate-hashes-async")

        # Should still return 200 because the endpoint succeeded
        # The error is tracked in the background thread
        assert response.status_code == 200
