"""
Extended tests for Audible integration operations module.

Tests background thread worker functions for download, genre sync, and
narrator sync operations including subprocess.Popen mocking, progress
parsing via char-by-char reading, and error handling paths.
"""

import subprocess
import time
from io import StringIO
from unittest.mock import MagicMock, patch


MODULE = "backend.api_modular.utilities_ops.audible"


def _make_mock_popen_charread(chars, returncode=0, stderr_text=""):
    """Create a mock Popen whose stdout.read(1) yields one char at a time.

    Used for download endpoint that reads char-by-char.
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


class TestDownloadAudiobooksWorkerThread:
    """Test the run_download() background thread function."""

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_success_with_items(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Successful download parses item progress and success count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-001"
        mock_get_tracker.return_value = mock_tracker

        output = (
            "[1/3] Downloading: Book One\n"
            "\u2713 Downloaded: Book One\n"
            "[2/3] Downloading: Book Two\n"
            "\u2713 Downloaded: Book Two\n"
            "[3/3] Downloading: Book Three\n"
            "\u2717 Failed: Book Three\n"
            "Download complete: 2 succeeded, 1 failed\n"
        )
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["downloaded_count"] == 2
        assert result["failed_count"] == 1
        assert result["total_attempted"] == 3

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_success_no_items(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Download with no items to download still completes."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-002"
        mock_get_tracker.return_value = mock_tracker

        output = "No new audiobooks to download\n"
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["downloaded_count"] == 0
        assert result["failed_count"] == 0

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_failure(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Non-zero return code fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-003"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen_charread(
            "", returncode=1, stderr_text="Authentication failed"
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        assert "Authentication failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_empty_stderr_fallback(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Empty stderr uses fallback message."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-004"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen_charread("", returncode=1, stderr_text="")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        assert "Download failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_timeout(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Timeout kills process and fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-005"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen_charread("", returncode=0)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="bash", timeout=3600)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        mock_proc.kill.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_generic_exception(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Generic exception calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-006"
        mock_get_tracker.return_value = mock_tracker

        mock_popen_cls.side_effect = OSError("Script not found")

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        assert "Script not found" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_output_truncation(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Output over 2000 chars is truncated."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-007"
        mock_get_tracker.return_value = mock_tracker

        long_output = ("[1/100] Downloading: " + "A" * 80 + "\n") * 50
        mock_proc = _make_mock_popen_charread(long_output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert len(result["output"]) <= 2000

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_carriage_return_handling(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Carriage returns are handled as line breaks."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-008"
        mock_get_tracker.return_value = mock_tracker

        # Use \r instead of \n for some lines
        output = "[1/2] Downloading: Book\r\u2713 Downloaded: Book\n"
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        mock_tracker.complete_operation.assert_called_once()

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_download_title_truncation(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Long titles are truncated to 50 chars in progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dl-009"
        mock_get_tracker.return_value = mock_tracker

        long_title = "A" * 100
        output = f"[1/1] Downloading: {long_title}\n"
        mock_proc = _make_mock_popen_charread(output, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/download-audiobooks-async")

        _wait_for_thread_completion(mock_tracker)
        # Verify progress was updated with truncated title
        progress_calls = mock_tracker.update_progress.call_args_list
        title_updates = [c for c in progress_calls if "Downloading:" in str(c)]
        if title_updates:
            msg = title_updates[0][0][2]
            # Title portion should be <= 50 chars
            assert len(msg) < 100


class TestSyncGenresWorkerThread:
    """Test the run_sync() background thread for genres."""

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_success(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Successful genre sync parses update count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-001"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [
                "Loading 200 audiobooks",
                "[100/200] Processing...",
                "[200/200] Processing...",
                "updated 45",
            ],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={"dry_run": False})

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["genres_updated"] == 45
        assert result["dry_run"] is False

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_dry_run_would_update(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Dry run parses 'would update' pattern."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-002"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Loading 50 audiobooks", "would update 12"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={"dry_run": True})

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["genres_updated"] == 12
        assert result["dry_run"] is True

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_failure(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Non-zero rc fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-003"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [], returncode=1, stderr_text="Metadata file not found"
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={})

        _wait_for_thread_completion(mock_tracker)
        assert "Metadata file not found" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_empty_stderr_fallback(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Empty stderr uses fallback."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-004"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=1, stderr_text="")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={})

        _wait_for_thread_completion(mock_tracker)
        assert "Genre sync failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_timeout(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Timeout kills process."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-005"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=0)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="python", timeout=600
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={})

        _wait_for_thread_completion(mock_tracker)
        mock_proc.kill.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_execute_flag(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Execute mode appends --execute flag."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-006"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={"dry_run": False})

        _wait_for_thread_completion(mock_tracker)
        cmd_args = mock_popen_cls.call_args[0][0]
        assert "--execute" in cmd_args

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_genre_sync_generic_exception(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Generic exception calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "genre-w-007"
        mock_get_tracker.return_value = mock_tracker

        mock_popen_cls.side_effect = RuntimeError("unexpected")

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-genres-async", json={})

        _wait_for_thread_completion(mock_tracker)
        assert "unexpected" in mock_tracker.fail_operation.call_args[0][1]


class TestSyncNarratorsWorkerThread:
    """Test the run_sync() background thread for narrators."""

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_success(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Successful narrator sync parses update count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-001"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [
                "Loading 300 audiobooks",
                "[150/300] Processing...",
                "[300/300] Processing...",
                "updated 80",
            ],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={"dry_run": False})

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["narrators_updated"] == 80
        assert result["dry_run"] is False

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_dry_run(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Dry run parses 'would update' count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-002"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Loading 100 audiobooks", "would update 15"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={"dry_run": True})

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["narrators_updated"] == 15

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_failure(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Non-zero rc fails operation with stderr."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-003"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [], returncode=1, stderr_text="DB connection error"
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={})

        _wait_for_thread_completion(mock_tracker)
        assert "DB connection error" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_empty_stderr_fallback(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Empty stderr uses fallback."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-004"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=1, stderr_text="")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={})

        _wait_for_thread_completion(mock_tracker)
        assert "Narrator sync failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_timeout(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Timeout kills process."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-005"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=0)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="python", timeout=600
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={})

        _wait_for_thread_completion(mock_tracker)
        mock_proc.kill.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_execute_flag(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Execute mode appends --execute."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-006"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={"dry_run": False})

        _wait_for_thread_completion(mock_tracker)
        cmd_args = mock_popen_cls.call_args[0][0]
        assert "--execute" in cmd_args

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_generic_exception(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Generic exception calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-007"
        mock_get_tracker.return_value = mock_tracker

        mock_popen_cls.side_effect = ValueError("bad args")

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={})

        _wait_for_thread_completion(mock_tracker)
        assert "bad args" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_output_truncation(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Output over 2000 chars is truncated."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-008"
        mock_get_tracker.return_value = mock_tracker

        long_lines = [f"[{i}/1000] Processing narrator" for i in range(100)]
        mock_proc = _make_mock_popen(long_lines, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={})

        _wait_for_thread_completion(mock_tracker)
        result = mock_tracker.complete_operation.call_args[0][1]
        assert len(result["output"]) <= 2000

    @patch(f"{MODULE}.subprocess.Popen")
    @patch(f"{MODULE}.get_tracker")
    def test_narrator_sync_loading_count(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Loading pattern updates progress to 10%."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "narr-w-009"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Loading 500 audiobooks"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/sync-narrators-async", json={})

        _wait_for_thread_completion(mock_tracker)
        progress_calls = mock_tracker.update_progress.call_args_list
        percents = [c[0][1] for c in progress_calls]
        assert 10 in percents


class TestCheckAudiblePrereqsExtended:
    """Additional tests for the check_audible_prereqs endpoint."""

    @patch(f"{MODULE}.os.path.isfile")
    @patch(f"{MODULE}.os.environ.get")
    def test_returns_data_dir_in_response(self, mock_env_get, mock_isfile, flask_app):
        """Response includes data_dir regardless of metadata existence."""
        mock_env_get.return_value = "/custom/data/path"
        mock_isfile.return_value = False

        with flask_app.test_client() as client:
            resp = client.get("/api/utilities/check-audible-prereqs")

        data = resp.get_json()
        assert data["data_dir"] == "/custom/data/path"

    @patch(f"{MODULE}.os.path.isfile")
    @patch(f"{MODULE}.os.environ.get")
    def test_metadata_path_includes_filename(
        self, mock_env_get, mock_isfile, flask_app
    ):
        """Metadata path ends with library_metadata.json."""
        mock_env_get.return_value = "/srv/audiobooks"
        mock_isfile.return_value = True

        with flask_app.test_client() as client:
            resp = client.get("/api/utilities/check-audible-prereqs")

        data = resp.get_json()
        assert data["library_metadata_path"].endswith("library_metadata.json")

    @patch(f"{MODULE}.os.path.isfile")
    def test_uses_default_data_dir(self, mock_isfile, flask_app):
        """Uses /srv/audiobooks as default data dir."""
        mock_isfile.return_value = False

        with flask_app.test_client() as client:
            with patch.dict("os.environ", {}, clear=False):
                # Remove AUDIOBOOKS_DATA if present to test default
                import os

                old_val = os.environ.pop("AUDIOBOOKS_DATA", None)
                try:
                    resp = client.get("/api/utilities/check-audible-prereqs")
                finally:
                    if old_val is not None:
                        os.environ["AUDIOBOOKS_DATA"] = old_val

        data = resp.get_json()
        # Default is /srv/audiobooks
        assert "audiobooks" in data["data_dir"]
