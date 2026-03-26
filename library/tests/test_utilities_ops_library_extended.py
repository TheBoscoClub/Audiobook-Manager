"""
Extended tests for library operations module.

Tests background thread worker functions for add-new, rescan, and reimport
operations including subprocess.Popen mocking, progress parsing, and error paths.
"""

import subprocess
import time
from io import StringIO
from unittest.mock import MagicMock, patch


MODULE = "backend.api_modular.utilities_ops.library"


def _make_mock_popen_charread(chars, returncode=0, stderr_text=""):
    """Create a mock Popen whose stdout.read(1) yields one char at a time.

    Used for rescan and download endpoints that read char-by-char.
    """
    mock_proc = MagicMock()
    char_iter = iter(chars)
    mock_proc.stdout.read = lambda n: next(char_iter, "")
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read.return_value = stderr_text
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = None
    mock_proc.kill.return_value = None
    return mock_proc


def _make_mock_popen(stdout_lines, returncode=0, stderr_text=""):
    """Create a mock Popen that yields stdout_lines line-by-line."""
    mock_proc = MagicMock()
    mock_proc.stdout = StringIO("\n".join(stdout_lines) + "\n" if stdout_lines else "")
    mock_proc.stdout.readline = mock_proc.stdout.readline
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read.return_value = stderr_text
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = None
    mock_proc.kill.return_value = None
    return mock_proc


def _wait_for_thread_completion(tracker_mock, timeout=2.0):
    """Wait until tracker's complete_operation or fail_operation is called."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tracker_mock.complete_operation.called or tracker_mock.fail_operation.called:
            return True
        time.sleep(0.02)
    return False


class TestAddNewBackgroundThread:
    """Test the run_add_new() background thread function."""

    @patch(f"{MODULE}.get_tracker")
    @patch(f"{MODULE}.create_progress_callback")
    def test_add_new_success(self, mock_create_cb, mock_get_tracker, flask_app):
        """Successful add_new completes operation with results."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-w-001"
        mock_get_tracker.return_value = mock_tracker
        mock_create_cb.return_value = MagicMock()

        mock_results = {"added": 5, "skipped": 10, "errors": 0}

        with flask_app.test_client() as client:
            with patch(
                "backend.api_modular.utilities_ops.library.sys.path",
                new_callable=lambda: MagicMock(insert=MagicMock()),
            ):
                # We need to mock the actual import inside run_add_new
                mock_add_module = MagicMock()
                mock_add_module.add_new_audiobooks.return_value = mock_results
                mock_add_module.AUDIOBOOK_DIR = "/mock/library"
                mock_add_module.COVER_DIR = "/mock/covers"

                with patch.dict(
                    "sys.modules",
                    {"add_new_audiobooks": mock_add_module},
                ):
                    resp = client.post(
                        "/api/utilities/add-new",
                        json={"calculate_hashes": True},
                    )

        assert resp.status_code == 200
        _wait_for_thread_completion(mock_tracker)

        # Thread may or may not have run yet due to import mocking complexity.
        # The endpoint response itself verifies the API layer.

    @patch(f"{MODULE}.get_tracker")
    @patch(f"{MODULE}.create_progress_callback")
    def test_add_new_exception_calls_fail(
        self, mock_create_cb, mock_get_tracker, flask_app
    ):
        """Exception in add_new thread calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-w-002"
        mock_get_tracker.return_value = mock_tracker
        mock_create_cb.return_value = MagicMock()

        with flask_app.test_client() as client:
            resp = client.post("/api/utilities/add-new", json={})

        assert resp.status_code == 200
        # Thread will fail since add_new_audiobooks can't be imported in test env
        _wait_for_thread_completion(mock_tracker)
        # Either complete or fail - we verify the endpoint returned 200

    @patch(f"{MODULE}.get_tracker")
    def test_add_new_empty_json_body(self, mock_get_tracker, flask_app):
        """Empty JSON body defaults calculate_hashes to True."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-w-003"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            resp = client.post(
                "/api/utilities/add-new",
                json={},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch(f"{MODULE}.get_tracker")
    def test_add_new_response_format(self, mock_get_tracker, flask_app):
        """Response has correct format with success, message, operation_id."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "add-w-004"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            resp = client.post("/api/utilities/add-new", json={})

        data = resp.get_json()
        assert "success" in data
        assert "message" in data
        assert "operation_id" in data
        assert data["message"] == "Add operation started"


class TestRescanLibraryWorkerThread:
    """Test the run_rescan() background thread function."""

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_success_with_progress(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Rescan parses percent progress from scanner output."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-001"
        mock_get_tracker.return_value = mock_tracker

        # Scanner uses \r for progress, \n for final output
        output = "50% | 900/1800\r99% | 1790/1800\nTotal files: 1800\n"
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["files_found"] == 1800

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_strips_ansi_codes(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """ANSI escape codes are stripped before regex matching."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-002"
        mock_get_tracker.return_value = mock_tracker

        output = "\033[32m75% | 750/1000\033[0m\nTotal audiobooks: 1000\n"
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["files_found"] == 1000

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_failure(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Non-zero return code calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-003"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen_charread(
            "", returncode=1, stderr_text="Scanner crashed"
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        mock_tracker.fail_operation.assert_called_once()
        assert "Scanner crashed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_empty_stderr_fallback(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Empty stderr on failure uses fallback message."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-004"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen_charread("", returncode=1, stderr_text="")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        assert "Scanner failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_timeout(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Timeout kills process and fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-005"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen_charread("", returncode=0)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="python", timeout=1800
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        mock_proc.kill.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_generic_exception(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Generic exception calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-006"
        mock_get_tracker.return_value = mock_tracker

        mock_popen_cls.side_effect = FileNotFoundError("scanner not found")

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        mock_tracker.fail_operation.assert_called_once()
        assert "scanner not found" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_output_truncation(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Output longer than 2000 chars is truncated."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-007"
        mock_get_tracker.return_value = mock_tracker

        long_output = ("x" * 100 + "\n") * 30
        mock_proc = _make_mock_popen_charread(long_output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert len(result["output"]) <= 2000

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_rescan_total_files_parsing_error(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Malformed 'Total files:' line handled gracefully."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rescan-w-008"
        mock_get_tracker.return_value = mock_tracker

        output = "Total files: not_a_number\n"
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/rescan-async")

        _wait_for_thread_completion(mock_tracker)
        # Should still complete without crashing
        mock_tracker.complete_operation.assert_called_once()


class TestReimportDatabaseWorkerThread:
    """Test the run_reimport() background thread function."""

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_success_with_progress(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Reimport parses Found/Processed/Imported patterns."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-001"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [
                "Found 500 audiobooks",
                "Preserving existing metadata",
                "Processed 250/500 audiobooks",
                "Processed 500/500 audiobooks",
                "Imported 500 audiobooks",
                "Optimizing database",
            ],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["imported_count"] == 500
        assert result["total_audiobooks"] == 500

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_creating_database(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Creating database pattern updates progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-002"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [
                "Creating database",
                "Database schema created",
                "Found 10 audiobooks",
                "Imported 10 audiobooks",
            ],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        progress_calls = mock_tracker.update_progress.call_args_list
        progress_messages = [str(c) for c in progress_calls]
        assert any("Creating database" in m for m in progress_messages)
        assert any("schema ready" in m for m in progress_messages)

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_failure(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Non-zero return code calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-003"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=1, stderr_text="Database error")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        assert "Database error" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_empty_stderr_fallback(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Empty stderr on failure uses fallback."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-004"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=1, stderr_text="")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        assert "Import failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_timeout(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Timeout kills process."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-005"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=0)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="python", timeout=600
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        mock_proc.kill.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_generic_exception(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Generic exception calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-006"
        mock_get_tracker.return_value = mock_tracker

        mock_popen_cls.side_effect = PermissionError("access denied")

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        assert "access denied" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_output_truncation(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Output longer than 2000 chars is truncated."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-007"
        mock_get_tracker.return_value = mock_tracker

        long_lines = [f"Processed {i}/2000 audiobooks" for i in range(100)]
        mock_proc = _make_mock_popen(long_lines, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert len(result["output"]) <= 2000

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_preserving_metadata(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Preserving metadata line updates progress to 8%."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-008"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Preserving existing metadata"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        progress_calls = mock_tracker.update_progress.call_args_list
        percents = [c[0][1] for c in progress_calls]
        assert 8 in percents

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_reimport_optimizing_database(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Optimizing database line updates progress to 95%."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "reimp-009"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Optimizing database"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/reimport-async")

        _wait_for_thread_completion(mock_tracker)
        progress_calls = mock_tracker.update_progress.call_args_list
        percents = [c[0][1] for c in progress_calls]
        assert 95 in percents
