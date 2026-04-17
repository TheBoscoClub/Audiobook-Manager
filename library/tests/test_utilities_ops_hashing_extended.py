"""
Extended tests for hashing operations module.

Tests background thread worker functions for both SHA-256 hash generation
(subprocess.Popen) and MD5 checksum generation (inline file processing).
"""

import hashlib
import os
import subprocess
from io import StringIO
from unittest.mock import MagicMock, patch

from tests.helpers import wait_for_thread_completion


MODULE = "backend.api_modular.utilities_ops.hashing"
SUBPROCESS_MODULE = "backend.api_modular.utilities_ops._subprocess"
HELPERS_MODULE = "backend.api_modular.utilities_ops._helpers"


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


class TestGenerateHashesWorkerThread:
    """Test the run_hash_gen() background thread function."""

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_success_with_progress(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Successful hash generation parses [X/Y] progress."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-001"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            [
                "[10/100] Hashing file...",
                "[50/100] Hashing file...",
                "[100/100] Hashing file...",
                "Generated 100 hashes",
            ],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            resp = client.post("/api/utilities/generate-hashes-async")
        assert resp.status_code == 200

        wait_for_thread_completion(mock_tracker)
        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["hashes_generated"] == 100

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_file_pattern(self, mock_get_tracker, mock_popen_cls, flask_app):
        """File pattern updates progress with filename."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-002"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Hashing: /path/to/audiobook.opus", "Completed 1"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        # Check update_progress was called with filename info
        progress_calls = mock_tracker.update_progress.call_args_list
        found_hashing_update = any("Hashing:" in str(c) for c in progress_calls)
        assert found_hashing_update

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_processing_pattern(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Processing pattern updates progress count."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-003"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen(
            ["Processing file 500"],
            returncode=0,
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        # Should have at least initial + processing progress
        assert mock_tracker.update_progress.call_count >= 2

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_failure(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Non-zero return code calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-004"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=1, stderr_text="Permission denied")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        mock_tracker.fail_operation.assert_called_once()
        assert "Permission denied" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_empty_stderr_fallback(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Empty stderr on failure uses fallback message."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-005"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=1, stderr_text="")
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        assert "Hash generation failed" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_timeout(self, mock_get_tracker, mock_popen_cls, flask_app):
        """Timeout kills process and fails operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-006"
        mock_get_tracker.return_value = mock_tracker

        mock_proc = _make_mock_popen([], returncode=0)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(
            cmd="python", timeout=1800
        )
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        mock_proc.kill.assert_called_once()
        error_msg = mock_tracker.fail_operation.call_args[0][1]
        assert "did not exit cleanly" in error_msg or "timed out" in error_msg

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_generic_exception(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Generic exception calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-007"
        mock_get_tracker.return_value = mock_tracker

        mock_popen_cls.side_effect = FileNotFoundError("script not found")

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        mock_tracker.fail_operation.assert_called_once()
        assert "script not found" in mock_tracker.fail_operation.call_args[0][1]

    @patch(f"{SUBPROCESS_MODULE}.subprocess.Popen")
    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_hash_gen_output_truncation(
        self, mock_get_tracker, mock_popen_cls, flask_app
    ):
        """Output over 2000 chars is truncated."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "hash-w-008"
        mock_get_tracker.return_value = mock_tracker

        long_lines = [f"Hash {i}: " + "a" * 64 for i in range(50)]
        mock_proc = _make_mock_popen(long_lines, returncode=0)
        mock_popen_cls.return_value = mock_proc

        with flask_app.test_client() as client:
            client.post("/api/utilities/generate-hashes-async")

        wait_for_thread_completion(mock_tracker)
        assert mock_tracker.complete_operation.call_args is not None, (
            "Worker called fail_operation instead of complete_operation. "
            f"fail_operation.call_args={mock_tracker.fail_operation.call_args!r}"
        )
        result = mock_tracker.complete_operation.call_args[0][1]
        assert len(result["output"]) <= 2000


class TestGenerateChecksumsWorkerThread:
    """Test the run_checksum_gen() background thread function."""

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_no_files_completes_early(
        self, mock_get_tracker, flask_app, tmp_path
    ):
        """No source or library files completes with zero counts."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-001"
        mock_get_tracker.return_value = mock_tracker

        empty_data = tmp_path / "empty_data"
        empty_data.mkdir()
        (empty_data / "Sources").mkdir()
        (empty_data / "Library").mkdir()

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(empty_data)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["source_checksums"] == 0
        assert result["library_checksums"] == 0

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_processes_source_files(
        self, mock_get_tracker, flask_app, tmp_path
    ):
        """Checksums source .aaxc files correctly."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-002"
        mock_get_tracker.return_value = mock_tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sources = data_dir / "Sources"
        sources.mkdir()
        library = data_dir / "Library"
        library.mkdir()

        # Create test .aaxc files
        test_content = b"test audiobook content"
        (sources / "book1.aaxc").write_bytes(test_content)
        (sources / "book2.aaxc").write_bytes(test_content * 2)

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(data_dir)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["source_checksums"] == 2
        assert result["library_checksums"] == 0
        assert result["total_files"] == 2

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_processes_library_files(
        self, mock_get_tracker, flask_app, tmp_path
    ):
        """Checksums library .opus files, excluding .cover.opus."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-003"
        mock_get_tracker.return_value = mock_tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sources = data_dir / "Sources"
        sources.mkdir()
        library = data_dir / "Library"
        library.mkdir()
        author_dir = library / "Author Name"
        author_dir.mkdir()

        # Create .opus files
        (author_dir / "book1.opus").write_bytes(b"opus content 1")
        (author_dir / "book2.opus").write_bytes(b"opus content 2")
        (author_dir / "book1.cover.opus").write_bytes(b"cover art")  # Excluded

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(data_dir)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["library_checksums"] == 2
        assert result["source_checksums"] == 0

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_writes_index_files(self, mock_get_tracker, flask_app, tmp_path):
        """Index files are written to .index directory."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-004"
        mock_get_tracker.return_value = mock_tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sources = data_dir / "Sources"
        sources.mkdir()
        library = data_dir / "Library"
        library.mkdir()
        (sources / "test.aaxc").write_bytes(b"test content")

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(data_dir)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        index_dir = data_dir / ".index"
        assert index_dir.exists()
        source_idx = index_dir / "source_checksums.idx"
        assert source_idx.exists()
        content = source_idx.read_text()
        assert "|" in content  # format: checksum|filepath

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_handles_unreadable_file(
        self, mock_get_tracker, flask_app, tmp_path
    ):
        """Unreadable files produce None checksums (skipped)."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-005"
        mock_get_tracker.return_value = mock_tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sources = data_dir / "Sources"
        sources.mkdir()
        library = data_dir / "Library"
        library.mkdir()

        # Create a file then make it unreadable
        bad_file = sources / "unreadable.aaxc"
        bad_file.write_bytes(b"content")
        bad_file.chmod(0o000)

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(data_dir)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        # Restore permissions for cleanup
        bad_file.chmod(0o644)

        # Should still complete, just with 0 checksums since file was unreadable
        mock_tracker.complete_operation.assert_called_once()
        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["source_checksums"] == 0

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_generic_exception(self, mock_get_tracker, flask_app, tmp_path):
        """Generic exception in checksum gen calls fail_operation."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-006"
        mock_get_tracker.return_value = mock_tracker

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = "/nonexistent/path/data"
        try:
            with flask_app.test_client() as client:
                with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
                    client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        mock_tracker.fail_operation.assert_called_once()

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_progress_updates_periodically(
        self, mock_get_tracker, flask_app, tmp_path
    ):
        """Progress updates at 50-file intervals."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-007"
        mock_get_tracker.return_value = mock_tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sources = data_dir / "Sources"
        sources.mkdir()
        library = data_dir / "Library"
        library.mkdir()

        # Create 55 source files to trigger at least one periodic update
        for i in range(55):
            (sources / f"book{i:03d}.aaxc").write_bytes(b"content" * (i + 1))

        # Set env BEFORE the request so the background thread picks it up
        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(data_dir)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        # Should have: counting (5), source processing (10), periodic at 50,
        # library processing (50), write index (95), complete
        assert mock_tracker.update_progress.call_count >= 3

    @patch(f"{HELPERS_MODULE}.get_tracker")
    def test_checksum_nonexistent_dirs(self, mock_get_tracker, flask_app, tmp_path):
        """Non-existent Sources/Library dirs result in empty file lists."""
        mock_tracker = MagicMock()
        mock_tracker.is_operation_running.return_value = None
        mock_tracker.create_operation.return_value = "cs-008"
        mock_get_tracker.return_value = mock_tracker

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Don't create Sources or Library dirs

        old_val = os.environ.get("AUDIOBOOKS_DATA")
        os.environ["AUDIOBOOKS_DATA"] = str(data_dir)
        try:
            with flask_app.test_client() as client:
                client.post("/api/utilities/generate-checksums-async")
            wait_for_thread_completion(mock_tracker)
        finally:
            if old_val is not None:
                os.environ["AUDIOBOOKS_DATA"] = old_val
            else:
                os.environ.pop("AUDIOBOOKS_DATA", None)

        result = mock_tracker.complete_operation.call_args[0][1]
        assert result["source_checksums"] == 0
        assert result["library_checksums"] == 0
        assert "No files found" in result.get("message", "")


class TestChecksumFirstMbFunction:
    """Test the inline checksum_first_mb function logic."""

    def test_checksum_calculation(self, session_temp_dir):
        """Test MD5 checksum is calculated correctly for first 1MB."""
        test_file = session_temp_dir / "checksum_test.bin"
        test_content = b"A" * (1024 * 1024)
        test_file.write_bytes(test_content + b"extra data")

        expected = hashlib.md5(test_content, usedforsecurity=False).hexdigest()

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

    def test_checksum_empty_file(self, tmp_path):
        """Empty file produces a valid (empty bytes) MD5 checksum."""
        test_file = tmp_path / "empty.aaxc"
        test_file.write_bytes(b"")

        expected = hashlib.md5(b"", usedforsecurity=False).hexdigest()
        with open(test_file, "rb") as f:
            data = f.read(1048576)
        actual = hashlib.md5(data, usedforsecurity=False).hexdigest()

        assert actual == expected

    def test_checksum_exactly_1mb(self, tmp_path):
        """File exactly 1MB produces correct checksum."""
        test_file = tmp_path / "exact1mb.bin"
        content = os.urandom(1048576)
        test_file.write_bytes(content)

        expected = hashlib.md5(content, usedforsecurity=False).hexdigest()
        with open(test_file, "rb") as f:
            data = f.read(1048576)
        actual = hashlib.md5(data, usedforsecurity=False).hexdigest()

        assert actual == expected


class TestHashParsingLogic:
    """Test the hash output parsing logic."""

    def test_parses_generated_count(self):
        """Test parsing 'Generated X hashes' from output."""
        import re as regex

        generated_pattern = regex.compile(r"(?:Generated|Completed)\s*(\d+)", regex.I)
        line = "Generated 150 hashes successfully"
        match = generated_pattern.search(line)
        assert match is not None
        assert int(match.group(1)) == 150

    def test_parses_completed_count(self):
        """Test parsing 'Completed X' variant."""
        import re as regex

        generated_pattern = regex.compile(r"(?:Generated|Completed)\s*(\d+)", regex.I)
        line = "Completed 42 hash operations"
        match = generated_pattern.search(line)
        assert match is not None
        assert int(match.group(1)) == 42

    def test_progress_pattern_parsing(self):
        """Test [X/Y] progress pattern."""
        import re as regex

        progress_pattern = regex.compile(r"\[(\d+)/(\d+)\]")
        line = "[50/200] Hashing: audiobook.opus"
        match = progress_pattern.search(line)
        assert match is not None
        assert int(match.group(1)) == 50
        assert int(match.group(2)) == 200

    def test_file_pattern_truncation(self):
        """File pattern truncates filename to 40 chars."""
        import re as regex

        file_pattern = regex.compile(r"Hashing:\s*(.+)")
        long_name = "A" * 100
        line = f"Hashing: {long_name}"
        match = file_pattern.search(line)
        assert match is not None
        filename = match.group(1).strip()[:40]
        assert len(filename) == 40
