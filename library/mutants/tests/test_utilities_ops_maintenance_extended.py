"""
Extended tests for maintenance operations module.

Tests background thread worker functions with run_with_progress mocking,
regex-based progress parsing, and error handling paths.
"""

import subprocess
from unittest.mock import MagicMock, patch

from tests.helpers import wait_for_thread_completion

MODULE = "backend.api_modular.utilities_ops.maintenance"
HELPERS_MODULE = "backend.api_modular.utilities_ops._helpers"


def _make_side_effect(stdout_lines, returncode=0, stderr_text="", timed_out=False):
    """Create a side_effect function that invokes line_callback then returns result.

    Builds the result dict using operation_name from the actual call, matching
    real run_with_progress behavior.
    """

    def side_effect(cmd, *, line_callback, timeout_secs, operation_name="Operation", env=None):
        # Invoke line_callback for each line so regex parsing is exercised
        for line_text in stdout_lines:
            line_callback(line_text)

        output = "\n".join(stdout_lines)
        success = returncode == 0 and not timed_out

        if timed_out:
            error = f"{operation_name} timed out after 0 minutes"
        elif not success:
            error = stderr_text or f"{operation_name} failed"
        else:
            error = None

        return {
            "success": success,
            "output": output[-2000:] if len(output) > 2000 else output,
            "stderr": stderr_text,
            "returncode": returncode,
            "timed_out": timed_out,
            "error": error,
        }

    return side_effect


def _make_side_effect_raises(exc):
    """Create a side_effect function that raises an exception."""

    def side_effect(cmd, *, line_callback, timeout_secs, operation_name="Operation", env=None):
        raise exc

    return side_effect


class TestRebuildQueueWorkerThread:
    """Test the run_rebuild() background thread function."""

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_successful_rebuild_completes_operation(self, mock_get_tracker, mock_rwp, flask_app):
        """Successful rebuild calls complete_operation with queue_size."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-001"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            ["Scanning sources...", "Found 42 files", "Queue size: 42"], returncode=0
        )

        with flask_app.test_client() as client:
            resp = client.post("/api/utilities/rebuild-queue-async")
        assert resp.status_code == 200

        wait_for_thread_completion(mock_tracker, expect="complete")
        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["queue_size"] == 42

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_rebuild_failure_calls_fail_operation(self, mock_get_tracker, mock_rwp, flask_app):
        """Non-zero return code calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-002"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=1, stderr_text="Script error")

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "Script error" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_rebuild_timeout_kills_process(self, mock_get_tracker, mock_rwp, flask_app):
        """Timeout fails the operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-003"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], timed_out=True)

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_rebuild_generic_exception(self, mock_get_tracker, mock_rwp, flask_app):
        """Generic exception in run_rebuild calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-004"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect_raises(OSError("No such file"))

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "No such file" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_rebuild_scanning_progress_updates(self, mock_get_tracker, mock_rwp, flask_app):
        """Scanning pattern updates progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-005"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            ["Scanning directory 500", "Found 200 files", "Queue rebuilt: 150"], returncode=0
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        wait_for_thread_completion(mock_tracker)
        # Should have multiple update_progress calls for scanning + found
        progress_calls = mock_tracker.update_progress.call_args_list
        assert len(progress_calls) >= 2  # Initial + at least scanning/found

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_rebuild_output_truncation(self, mock_get_tracker, mock_rwp, flask_app):
        """Output longer than 2000 chars is truncated."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-006"
        mock_get_tracker.return_value = mock_tracker

        long_lines = [f"Line {i}: " + "x" * 100 for i in range(50)]
        mock_rwp.side_effect = _make_side_effect(long_lines, returncode=0)

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        wait_for_thread_completion(mock_tracker, expect="complete")
        result = mock_tracker.complete_operation.call_args[0][1]
        assert len(result["output"]) <= 2000

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_rebuild_empty_stderr_fallback(self, mock_get_tracker, mock_rwp, flask_app):
        """Non-zero rc with empty stderr uses fallback message."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "rb-007"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=1, stderr_text="")

        with flask_app.test_client() as client:
            client.post("/api/utilities/rebuild-queue-async")

        wait_for_thread_completion(mock_tracker, expect="fail")
        assert "Queue rebuild failed" in mock_tracker.fail_operation.call_args[0][1]


class TestCleanupIndexesWorkerThread:
    """Test the run_cleanup() background thread function."""

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_cleanup_success_with_progress_pattern(self, mock_get_tracker, mock_rwp, flask_app):
        """Cleanup processes [X/Y] progress pattern correctly."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cl-001"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            [
                "[10/100] Checking entry...",
                "[50/100] Checking entry...",
                "[100/100] Checking entry...",
                "removed 5 stale entries",
            ],
            returncode=0,
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/cleanup-indexes-async", json={"dry_run": True})

        wait_for_thread_completion(mock_tracker, expect="complete")
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["entries_removed"] == 5
        assert result["dry_run"] is True

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_cleanup_checking_pattern(self, mock_get_tracker, mock_rwp, flask_app):
        """Cleanup handles Checking/Verifying pattern."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cl-002"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            ["Checking entry 500", "Verifying index 1000", "would remove 3 stale"], returncode=0
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/cleanup-indexes-async", json={"dry_run": True})

        wait_for_thread_completion(mock_tracker, expect="complete")
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["entries_removed"] == 3

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_cleanup_timeout(self, mock_get_tracker, mock_rwp, flask_app):
        """Cleanup timeout fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cl-003"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], timed_out=True)

        with flask_app.test_client() as client:
            client.post("/api/utilities/cleanup-indexes-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_cleanup_execute_mode_command(self, mock_get_tracker, mock_rwp, flask_app):
        """Execute mode does not append --dry-run flag."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cl-004"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=0)

        with flask_app.test_client() as client:
            client.post("/api/utilities/cleanup-indexes-async", json={"dry_run": False})

        wait_for_thread_completion(mock_tracker)
        cmd_args = mock_rwp.call_args[0][0]
        assert "--dry-run" not in cmd_args


class TestPopulateSortFieldsWorkerThread:
    """Test the run_populate() background thread function for sort fields."""

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_sort_fields_success_with_loading(self, mock_get_tracker, mock_rwp, flask_app):
        """Sort field population parses loading and update count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sf-001"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            [
                "Loading 500 audiobooks",
                "[100/500] Processing...",
                "[500/500] Processing...",
                "updated 42",
            ],
            returncode=0,
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/populate-sort-fields-async", json={"dry_run": False})

        wait_for_thread_completion(mock_tracker, expect="complete")
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["fields_updated"] == 42
        assert result["dry_run"] is False

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_sort_fields_dry_run_would_update(self, mock_get_tracker, mock_rwp, flask_app):
        """Dry run parses 'would update' count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sf-002"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            ["Loading 100 audiobooks", "would update 25"], returncode=0
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/populate-sort-fields-async", json={"dry_run": True})

        wait_for_thread_completion(mock_tracker, expect="complete")
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["fields_updated"] == 25

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_sort_fields_failure(self, mock_get_tracker, mock_rwp, flask_app):
        """Sort field population failure uses stderr."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sf-003"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=1, stderr_text="DB locked")

        with flask_app.test_client() as client:
            client.post("/api/utilities/populate-sort-fields-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        assert "DB locked" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_sort_fields_timeout(self, mock_get_tracker, mock_rwp, flask_app):
        """Sort field timeout."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sf-004"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], timed_out=True)

        with flask_app.test_client() as client:
            client.post("/api/utilities/populate-sort-fields-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_sort_fields_execute_appends_flag(self, mock_get_tracker, mock_rwp, flask_app):
        """Execute mode appends --execute flag."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sf-005"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=0)

        with flask_app.test_client() as client:
            client.post("/api/utilities/populate-sort-fields-async", json={"dry_run": False})

        wait_for_thread_completion(mock_tracker)
        cmd_args = mock_rwp.call_args[0][0]
        assert "--execute" in cmd_args

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_sort_fields_processing_pattern(self, mock_get_tracker, mock_rwp, flask_app):
        """Processing pattern updates progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "sf-006"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(["Processing title 500"], returncode=0)

        with flask_app.test_client() as client:
            client.post("/api/utilities/populate-sort-fields-async", json={})

        wait_for_thread_completion(mock_tracker)
        # At least the initial progress + processing pattern
        assert mock_tracker.update_progress.call_count >= 2


class TestPopulateAsinsWorkerThread:
    """Test the run_populate() background thread for ASIN population."""

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{MODULE}.subprocess.run")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_asin_populate_success(self, mock_get_tracker, mock_sub_run, mock_rwp, flask_app):
        """Successful ASIN population with export + match steps."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "asin-001"
        mock_get_tracker.return_value = mock_tracker

        # Step 1: Export succeeds
        mock_export = MagicMock()
        mock_export.returncode = 0
        mock_export.stdout = ""
        mock_export.stderr = ""
        mock_sub_run.return_value = mock_export

        # Step 2: Match process via run_with_progress
        mock_rwp.side_effect = _make_side_effect(
            ["[10/50] Processing audiobook 10", "Matched: 30", "Unmatched: 20"], returncode=0
        )

        with flask_app.test_client() as client:
            with patch(f"{MODULE}.Path.exists", return_value=True):
                with patch(
                    f"{MODULE}.tempfile.mkstemp",
                    return_value=(3, "/tmp/test.json"),  # nosec B108  # test fixture path
                ):
                    with patch(f"{MODULE}.os.close"):
                        resp = client.post(
                            "/api/utilities/populate-asins-async", json={"dry_run": False}
                        )

        assert resp.status_code == 200
        wait_for_thread_completion(mock_tracker, expect="complete")
        if mock_tracker.complete_operation.called:
            result = mock_tracker.complete_operation.call_args[0][1]
            assert result["asins_matched"] == 30
            assert result["unmatched"] == 20

    @patch(f"{MODULE}.subprocess.run")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_asin_export_timeout(self, mock_get_tracker, mock_sub_run, flask_app):
        """Audible export timeout fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "asin-002"
        mock_get_tracker.return_value = mock_tracker

        mock_sub_run.side_effect = subprocess.TimeoutExpired(cmd="audible", timeout=300)

        with flask_app.test_client() as client:
            with patch(
                f"{MODULE}.tempfile.mkstemp",
                return_value=(3, "/tmp/test.json"),  # nosec B108  # test fixture path
            ):
                with patch(f"{MODULE}.os.close"):
                    client.post("/api/utilities/populate-asins-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.run")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_asin_export_failure(self, mock_get_tracker, mock_sub_run, flask_app):
        """Audible export non-zero rc fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "asin-003"
        mock_get_tracker.return_value = mock_tracker

        mock_export = MagicMock()
        mock_export.returncode = 1
        mock_export.stderr = "Auth failed"
        mock_export.stdout = ""
        mock_sub_run.return_value = mock_export

        with flask_app.test_client() as client:
            with patch(
                f"{MODULE}.tempfile.mkstemp",
                return_value=(3, "/tmp/test.json"),  # nosec B108  # test fixture path
            ):
                with patch(f"{MODULE}.os.close"):
                    client.post("/api/utilities/populate-asins-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "Auth failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.subprocess.run")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_asin_export_file_not_found(self, mock_get_tracker, mock_sub_run, flask_app):
        """Export completes but file not found fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "asin-004"
        mock_get_tracker.return_value = mock_tracker

        mock_export = MagicMock()
        mock_export.returncode = 0
        mock_export.stderr = ""
        mock_export.stdout = ""
        mock_sub_run.return_value = mock_export

        with flask_app.test_client() as client:
            with patch(
                f"{MODULE}.tempfile.mkstemp",
                return_value=(3, "/tmp/nonexist.json"),  # nosec B108  # test fixture path
            ):
                with patch(f"{MODULE}.os.close"):
                    client.post("/api/utilities/populate-asins-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "not found" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_asin_409_when_running(self, mock_get_tracker, flask_app):
        """Returns 409 when ASIN population already running."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = "existing-asin"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            resp = client.post("/api/utilities/populate-asins-async", json={})

        assert resp.status_code == 409
        data = resp.get_json()
        assert "ASIN population already in progress" in data["error"]


class TestFindSourceDuplicatesWorkerThread:
    """Test the run_scan() background thread for duplicate detection."""

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_success_with_progress(self, mock_get_tracker, mock_rwp, flask_app):
        """Duplicate scan parses progress and duplicate counts."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-001"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            ["Found 100 files", "[50/100] Comparing...", "duplicate groups: 5"], returncode=0
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={"dry_run": True})

        wait_for_thread_completion(mock_tracker, expect="complete")
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["duplicates_found"] == 5
        assert result["dry_run"] is True

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_scanning_progress(self, mock_get_tracker, mock_rwp, flask_app):
        """Scanning pattern tracks file count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-002"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(
            ["Scanning files 500", "Checking hash 1000"], returncode=0
        )

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={})

        wait_for_thread_completion(mock_tracker)
        assert mock_tracker.update_progress.call_count >= 2

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_failure(self, mock_get_tracker, mock_rwp, flask_app):
        """Duplicate scan failure uses stderr."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-003"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=1, stderr_text="Permission denied")

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        assert "Permission denied" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_timeout(self, mock_get_tracker, mock_rwp, flask_app):
        """Duplicate scan timeout fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-004"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], timed_out=True)

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        mock_tracker.fail_operation.assert_called_once()
        assert "timed out" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_found_files_progress(self, mock_get_tracker, mock_rwp, flask_app):
        """Found N files pattern updates progress to 20%."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-005"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect(["Found 250 sources to analyze"], returncode=0)

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={})

        wait_for_thread_completion(mock_tracker)
        # Check that update_progress was called with 20% for "Found X sources"
        progress_calls = mock_tracker.update_progress.call_args_list
        progress_percents = [c[0][1] for c in progress_calls]
        assert 20 in progress_percents

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_dry_run_appends_flag(self, mock_get_tracker, mock_rwp, flask_app):
        """Dry run appends --dry-run flag to command."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-006"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=0)

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={"dry_run": True})

        wait_for_thread_completion(mock_tracker)
        cmd_args = mock_rwp.call_args[0][0]
        assert "--dry-run" in cmd_args

    @patch(f"{MODULE}.run_with_progress")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_duplicates_empty_stderr_fallback(self, mock_get_tracker, mock_rwp, flask_app):
        """Empty stderr on failure uses fallback message."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "dup-007"
        mock_get_tracker.return_value = mock_tracker

        mock_rwp.side_effect = _make_side_effect([], returncode=1, stderr_text="")

        with flask_app.test_client() as client:
            client.post("/api/utilities/find-source-duplicates-async", json={})

        wait_for_thread_completion(mock_tracker, expect="fail")
        assert "Duplicate scan failed" in mock_tracker.fail_operation.call_args[0][1]


class TestPopulateAsinsEndpointMethodConstraints:
    """Test ASIN populate endpoint method constraints."""

    def test_populate_asins_only_post(self, flask_app):
        """Test populate-asins only allows POST."""
        with flask_app.test_client() as client:
            response = client.get("/api/utilities/populate-asins-async")
        assert response.status_code == 405

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_populate_asins_defaults_dry_run(self, mock_get_tracker, flask_app):
        """ASIN populate defaults to dry_run=True."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "asin-default"
        mock_get_tracker.return_value = mock_tracker

        with flask_app.test_client() as client:
            resp = client.post("/api/utilities/populate-asins-async", json={})

        assert resp.status_code == 200
        data = resp.get_json()
        assert "dry run" in data["message"]
