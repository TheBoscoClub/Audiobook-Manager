"""
Tests for library/scripts/generate_hashes.py

Exercises all public functions with mocked filesystem and database.
The __main__ block and CLI entry point (main()) are excluded per instructions.
"""

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure library root is on path so generate_hashes can import common + config
LIBRARY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

# ---------------------------------------------------------------------------
# Module-level import setup: mock the `config` and `common` dependencies so
# the module can be imported without a real database or config file.
# ---------------------------------------------------------------------------

_FAKE_DB = Path("/tmp/fake_audiobooks.db")


def _import_generate_hashes():
    """Import generate_hashes with DB_PATH patched to a sentinel value."""
    mod_name = "scripts.generate_hashes"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    with patch("config.DATABASE_PATH", _FAKE_DB):
        import importlib

        mod = importlib.import_module(mod_name)
    return mod


@contextmanager
def _db_conn(db_path):
    """Open a sqlite3 connection and guarantee it is closed on exit.

    Unlike ``with sqlite3.connect(...) as conn:``, this context manager
    actually closes the connection (the stdlib context manager only
    commits/rolls back the transaction but leaves the connection open,
    triggering ResourceWarning under Python 3.14 strict GC).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_seconds_under_60(self):
        assert self.gh.format_duration(30) == "30s"

    def test_exactly_60_seconds(self):
        result = self.gh.format_duration(60)
        assert result == "1.0m"

    def test_minutes_under_3600(self):
        assert self.gh.format_duration(90) == "1.5m"

    def test_hours(self):
        assert self.gh.format_duration(7200) == "2.0h"

    def test_fractional_seconds(self):
        result = self.gh.format_duration(45.7)
        assert result == "46s"


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------


class TestFormatSize:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_bytes(self):
        assert self.gh.format_size(512) == "512.0B"

    def test_kilobytes(self):
        assert self.gh.format_size(2048) == "2.0KB"

    def test_megabytes(self):
        assert self.gh.format_size(1024 * 1024) == "1.0MB"

    def test_gigabytes(self):
        assert self.gh.format_size(1024 ** 3) == "1.0GB"

    def test_terabytes(self):
        assert self.gh.format_size(1024 ** 4) == "1.0TB"

    def test_petabytes(self):
        # Anything above TB overflows into PB
        assert self.gh.format_size(1024 ** 5) == "1.0PB"

    def test_float_input(self):
        result = self.gh.format_size(1536.0)
        assert result == "1.5KB"


# ---------------------------------------------------------------------------
# _ensure_hash_columns
# ---------------------------------------------------------------------------


class TestEnsureHashColumns:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_adds_columns_when_missing(self, tmp_path):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, file_path TEXT)")
            conn.commit()
            self.gh._ensure_hash_columns(conn)
            info = {row[1] for row in conn.execute("PRAGMA table_info(audiobooks)").fetchall()}
        assert "sha256_hash" in info
        assert "hash_verified_at" in info

    def test_idempotent_when_columns_exist(self, tmp_path):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.commit()
            # Should not raise
            self.gh._ensure_hash_columns(conn)


# ---------------------------------------------------------------------------
# get_pending_files
# ---------------------------------------------------------------------------


class TestGetPendingFiles:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def _setup_db(self, tmp_path):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, file_size_mb REAL, title TEXT, sha256_hash TEXT)"
            )
            conn.execute(
                "INSERT INTO audiobooks VALUES (1, '/a.opus', 100.0, 'BookA', NULL)"
            )
            conn.execute(
                "INSERT INTO audiobooks VALUES (2, '/b.opus', 200.0, 'BookB', 'abc123')"
            )
            conn.commit()
        return db

    def test_returns_only_unhashed_by_default(self, tmp_path):
        db = self._setup_db(tmp_path)
        with _db_conn(db) as conn:
            rows = self.gh.get_pending_files(conn, force=False)
        assert len(rows) == 1
        assert rows[0][0] == 1  # id=1 has no hash

    def test_force_returns_all(self, tmp_path):
        db = self._setup_db(tmp_path)
        with _db_conn(db) as conn:
            rows = self.gh.get_pending_files(conn, force=True)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# update_hash
# ---------------------------------------------------------------------------


class TestUpdateHash:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_sets_hash_and_timestamp(self, tmp_path):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.execute("INSERT INTO audiobooks VALUES (42, '/x.opus', NULL, NULL)")
            conn.commit()
            self.gh.update_hash(conn, 42, "deadbeef" * 8)
            row = conn.execute(
                "SELECT sha256_hash, hash_verified_at FROM audiobooks WHERE id=42"
            ).fetchone()
        assert row[0] == "deadbeef" * 8
        assert row[1] is not None  # timestamp set


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def _make_db(self, tmp_path, rows):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, "
                "title TEXT, file_size_mb REAL)"
            )
            for row in rows:
                conn.execute(
                    "INSERT INTO audiobooks (id, file_path, sha256_hash, title, file_size_mb) "
                    "VALUES (?, ?, ?, ?, ?)",
                    row,
                )
            conn.commit()
        return db

    def test_no_duplicates(self, tmp_path):
        db = self._make_db(
            tmp_path,
            [
                (1, "/a.opus", "hash1", "Book A", 100.0),
                (2, "/b.opus", "hash2", "Book B", 200.0),
            ],
        )
        with _db_conn(db) as conn:
            dupes = self.gh.find_duplicates(conn)
        assert dupes == []

    def test_finds_duplicate_pair(self, tmp_path):
        db = self._make_db(
            tmp_path,
            [
                (1, "/a.opus", "samehash", "Book A", 100.0),
                (2, "/b.opus", "samehash", "Book B", 100.0),
            ],
        )
        with _db_conn(db) as conn:
            dupes = self.gh.find_duplicates(conn)
        assert len(dupes) == 1
        assert dupes[0][1] == 2  # count=2

    def test_excludes_null_hashes(self, tmp_path):
        db = self._make_db(
            tmp_path,
            [
                (1, "/a.opus", None, "Book A", 100.0),
                (2, "/b.opus", None, "Book B", 100.0),
            ],
        )
        with _db_conn(db) as conn:
            dupes = self.gh.find_duplicates(conn)
        assert dupes == []


# ---------------------------------------------------------------------------
# hash_file_worker
# ---------------------------------------------------------------------------


class TestHashFileWorker:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_file_not_found(self, tmp_path):
        args = (1, str(tmp_path / "nonexistent.opus"), 100.0, "Missing Book")
        result = self.gh.hash_file_worker(args)
        assert result[1] is None
        assert result[4] == "File not found"

    def test_successful_hash(self, tmp_path):
        f = tmp_path / "book.opus"
        f.write_bytes(b"audiobook content")
        args = (2, str(f), 0.1, "Real Book")
        with patch.object(
            self.gh, "calculate_sha256", return_value="abc123def456"
        ):
            result = self.gh.hash_file_worker(args)
        assert result[1] == "abc123def456"
        assert result[4] is None  # no error

    def test_hash_calculation_failure(self, tmp_path):
        f = tmp_path / "book.opus"
        f.write_bytes(b"data")
        args = (3, str(f), 0.0, "Bad Book")
        with patch.object(self.gh, "calculate_sha256", return_value=None):
            result = self.gh.hash_file_worker(args)
        assert result[1] is None
        assert result[4] == "Hash calculation failed"


# ---------------------------------------------------------------------------
# generate_hash_for_book
# ---------------------------------------------------------------------------


class TestGenerateHashForBook:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def _make_db(self, tmp_path, rows=None):
        db = tmp_path / "audiobooks.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            if rows:
                for row in rows:
                    conn.execute(
                        "INSERT INTO audiobooks (id, file_path) VALUES (?, ?)", row
                    )
            conn.commit()
        return db

    def test_missing_row_returns_none(self, tmp_path):
        db = self._make_db(tmp_path)
        result = self.gh.generate_hash_for_book(999, db)
        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        db = self._make_db(tmp_path, [(1, str(tmp_path / "nonexistent.opus"))])
        result = self.gh.generate_hash_for_book(1, db)
        assert result is None

    def test_successful_hash_stored(self, tmp_path):
        opus = tmp_path / "book.opus"
        opus.write_bytes(b"fake opus data")
        db = self._make_db(tmp_path, [(1, str(opus))])
        with patch.object(self.gh, "calculate_sha256", return_value="aabbccdd" * 8):
            result = self.gh.generate_hash_for_book(1, db)
        assert result == "aabbccdd" * 8
        # Verify persisted
        with _db_conn(db) as conn:
            row = conn.execute(
                "SELECT sha256_hash FROM audiobooks WHERE id=1"
            ).fetchone()
        assert row[0] == "aabbccdd" * 8

    def test_hash_failure_returns_none(self, tmp_path):
        opus = tmp_path / "book.opus"
        opus.write_bytes(b"data")
        db = self._make_db(tmp_path, [(1, str(opus))])
        with patch.object(self.gh, "calculate_sha256", return_value=None):
            result = self.gh.generate_hash_for_book(1, db)
        assert result is None

    def test_empty_file_path_returns_none(self, tmp_path):
        db = self._make_db(tmp_path, [(1, "")])
        result = self.gh.generate_hash_for_book(1, db)
        assert result is None


# ---------------------------------------------------------------------------
# _compute_eta
# ---------------------------------------------------------------------------


class TestComputeEta:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_returns_calculating_on_first_file(self):
        import time

        result = self.gh._compute_eta(1, 0, 1000, time.time())
        assert result == "calculating..."

    def test_returns_duration_string(self):
        import time

        start = time.time() - 10  # 10 seconds elapsed
        result = self.gh._compute_eta(5, 500, 1000, start)
        # rate = 500/10 = 50 MB/s, remaining = 500, eta = 500/50 = 10s
        assert "s" in result or "m" in result


# ---------------------------------------------------------------------------
# _truncate_title
# ---------------------------------------------------------------------------


class TestTruncateTitle:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_short_title_unchanged(self):
        result = self.gh._truncate_title("Short Title")
        assert result == "Short Title"

    def test_long_title_truncated(self):
        long_title = "A" * 50
        result = self.gh._truncate_title(long_title)
        assert result.endswith("...")
        assert len(result) == 43  # 40 + "..."

    def test_custom_max_len(self):
        result = self.gh._truncate_title("Hello World", max_len=5)
        assert result == "Hello..."


# ---------------------------------------------------------------------------
# _print_hash_header and _print_hash_completion
# ---------------------------------------------------------------------------


class TestPrintHelpers:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_print_hash_header(self, capsys):
        self.gh._print_hash_header(10, 1024.0)
        out = capsys.readouterr().out
        assert "Files to process: 10" in out
        assert "Total size:" in out

    def test_print_hash_header_custom_label(self, capsys):
        self.gh._print_hash_header(5, 512.0, label="PARALLEL RUN")
        out = capsys.readouterr().out
        assert "PARALLEL RUN" in out

    def test_print_hash_completion_basic(self, capsys):
        self.gh._print_hash_completion(10, 1024.0, 30.0, 0)
        out = capsys.readouterr().out
        assert "Files processed: 10" in out
        assert "Errors: 0" in out

    def test_print_hash_completion_with_extra_lines(self, capsys):
        self.gh._print_hash_completion(5, 512.0, 15.0, 1, extra_lines=["Workers used: 4"])
        out = capsys.readouterr().out
        assert "Workers used: 4" in out

    def test_print_hash_completion_zero_elapsed(self, capsys):
        # elapsed=0 should not divide by zero
        self.gh._print_hash_completion(3, 300.0, 0.0, 0)
        out = capsys.readouterr().out
        assert "Files processed: 3" in out


# ---------------------------------------------------------------------------
# _process_sequential
# ---------------------------------------------------------------------------


class TestProcessSequential:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_processes_existing_file(self, tmp_path, capsys):
        opus = tmp_path / "book.opus"
        opus.write_bytes(b"test data")
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.execute("INSERT INTO audiobooks VALUES (1, ?, NULL, NULL)", (str(opus),))
            conn.commit()
            pending = [(1, str(opus), 0.1, "Test Book")]
            with patch.object(self.gh, "calculate_sha256", return_value="abc123"):
                processed, processed_size, errors, elapsed = self.gh._process_sequential(
                    pending, 1, 0.1, conn
                )
        assert processed == 1
        assert errors == 0

    def test_skips_missing_file(self, tmp_path, capsys):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.commit()
            pending = [(1, str(tmp_path / "missing.opus"), 0.5, "Missing Book")]
            processed, processed_size, errors, elapsed = self.gh._process_sequential(
                pending, 1, 0.5, conn
            )
        assert processed == 1
        assert errors == 1

    def test_null_file_size_treated_as_zero(self, tmp_path, capsys):
        opus = tmp_path / "book.opus"
        opus.write_bytes(b"data")
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.execute("INSERT INTO audiobooks VALUES (1, ?, NULL, NULL)", (str(opus),))
            conn.commit()
            pending = [(1, str(opus), None, "Null Size Book")]  # None size
            with patch.object(self.gh, "calculate_sha256", return_value="hash1"):
                processed, processed_size, errors, elapsed = self.gh._process_sequential(
                    pending, 1, 0, conn
                )
        assert processed == 1
        assert errors == 0


# ---------------------------------------------------------------------------
# show_stats
# ---------------------------------------------------------------------------


class TestShowStats:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def _make_db(self, tmp_path, rows=None):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, "
                "title TEXT, file_size_mb REAL)"
            )
            if rows:
                for row in rows:
                    conn.execute(
                        "INSERT INTO audiobooks (id, file_path, sha256_hash, title, file_size_mb) "
                        "VALUES (?, ?, ?, ?, ?)",
                        row,
                    )
            conn.commit()
        return db

    def test_no_duplicates_message(self, tmp_path, capsys):
        db = self._make_db(
            tmp_path,
            [(1, "/a.opus", "hash1", "Book A", 100.0)],
        )
        with _db_conn(db) as conn:
            self.gh.show_stats(conn)
        out = capsys.readouterr().out
        assert "No duplicates found" in out

    def test_stats_with_hashed_and_unhashed(self, tmp_path, capsys):
        db = self._make_db(
            tmp_path,
            [
                (1, "/a.opus", "hashA", "Book A", 100.0),
                (2, "/b.opus", None, "Book B", 200.0),
            ],
        )
        with _db_conn(db) as conn:
            self.gh.show_stats(conn)
        out = capsys.readouterr().out
        assert "Total audiobooks: 2" in out
        assert "With hashes: 1" in out

    def test_shows_duplicate_groups(self, tmp_path, capsys):
        db = self._make_db(
            tmp_path,
            [
                (1, "/a.opus", "duphash", "Book A", 100.0),
                (2, "/b.opus", "duphash", "Book B", 100.0),
            ],
        )
        with _db_conn(db) as conn:
            self.gh.show_stats(conn)
        out = capsys.readouterr().out
        assert "DUPLICATES FOUND" in out


# ---------------------------------------------------------------------------
# verify_hashes
# ---------------------------------------------------------------------------


class TestVerifyHashes:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def _make_db_with_hash(self, tmp_path, rows):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, "
                "title TEXT, file_size_mb REAL)"
            )
            for row in rows:
                conn.execute(
                    "INSERT INTO audiobooks (id, file_path, sha256_hash, title, file_size_mb) "
                    "VALUES (?, ?, ?, ?, ?)",
                    row,
                )
            conn.commit()
        return db

    def test_db_not_found_exits(self, tmp_path):
        missing = tmp_path / "missing.db"
        with patch.object(self.gh, "DB_PATH", missing):
            with pytest.raises(SystemExit):
                self.gh.verify_hashes()

    def test_no_hashed_files_returns_early(self, tmp_path, capsys):
        db = self._make_db_with_hash(tmp_path, [])
        with patch.object(self.gh, "DB_PATH", db):
            self.gh.verify_hashes()
        out = capsys.readouterr().out
        assert "No hashed files found" in out

    def test_missing_file_counted(self, tmp_path, capsys):
        db = self._make_db_with_hash(
            tmp_path,
            [(1, str(tmp_path / "missing.opus"), "abc123", "Missing Book", 10.0)],
        )
        with patch.object(self.gh, "DB_PATH", db):
            self.gh.verify_hashes(sample_size=1)
        out = capsys.readouterr().out
        assert "Missing files: 1" in out

    def test_hash_verified_correctly(self, tmp_path, capsys):
        opus = tmp_path / "verified.opus"
        opus.write_bytes(b"content")
        real_hash = "realhashhex1234"
        db = self._make_db_with_hash(
            tmp_path,
            [(1, str(opus), real_hash, "Verified Book", 0.1)],
        )
        with patch.object(self.gh, "DB_PATH", db):
            with patch.object(self.gh, "calculate_sha256", return_value=real_hash):
                self.gh.verify_hashes(sample_size=1)
        out = capsys.readouterr().out
        assert "Passed: 1" in out

    def test_hash_mismatch_detected(self, tmp_path, capsys):
        opus = tmp_path / "changed.opus"
        opus.write_bytes(b"changed content")
        stored = "storedhashabc"
        current = "differenthashdef"
        db = self._make_db_with_hash(
            tmp_path,
            [(1, str(opus), stored, "Mismatch Book", 0.1)],
        )
        with patch.object(self.gh, "DB_PATH", db):
            with patch.object(self.gh, "calculate_sha256", return_value=current):
                self.gh.verify_hashes(sample_size=1)
        out = capsys.readouterr().out
        assert "HASH MISMATCH" in out
        assert "Failed: 1" in out

    def test_hash_calc_failure_in_mismatch(self, tmp_path, capsys):
        """calculate_sha256 returning None for a changed file."""
        opus = tmp_path / "corrupt.opus"
        opus.write_bytes(b"data")
        db = self._make_db_with_hash(
            tmp_path,
            [(1, str(opus), "storehash", "Corrupt Book", 0.1)],
        )
        with patch.object(self.gh, "DB_PATH", db):
            with patch.object(self.gh, "calculate_sha256", return_value=None):
                self.gh.verify_hashes(sample_size=1)
        out = capsys.readouterr().out
        assert "failed to calculate" in out


# ---------------------------------------------------------------------------
# generate_hashes — happy path (no parallel, files exist)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# generate_hashes_parallel
# ---------------------------------------------------------------------------


class TestGenerateHashesParallel:
    """Test generate_hashes_parallel with mocked ProcessPoolExecutor to avoid forking."""

    def setup_method(self):
        self.gh = _import_generate_hashes()

    def _make_db_with_row(self, tmp_path):
        db = tmp_path / "test.db"
        opus = tmp_path / "book.opus"
        opus.write_bytes(b"data")
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, "
                "hash_verified_at TIMESTAMP, title TEXT, file_size_mb REAL)"
            )
            conn.execute(
                "INSERT INTO audiobooks VALUES (1, ?, NULL, NULL, 'Book', 0.1)",
                (str(opus),),
            )
            conn.commit()
        return db, opus

    def _mock_executor(self):
        """Return a mock ProcessPoolExecutor context manager with a resolved future."""
        from concurrent.futures import Future

        fut: Future = Future()
        fut.set_result((1, "abc123", "Book", 0.1, None))

        mock_executor = MagicMock()
        mock_executor.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor.__exit__ = MagicMock(return_value=False)
        mock_executor.submit = MagicMock(return_value=fut)
        return mock_executor

    def test_parallel_runs_to_completion(self, tmp_path, capsys):
        db, opus = self._make_db_with_row(tmp_path)
        pending = [(1, str(opus), 0.1, "Book")]
        mock_exec = self._mock_executor()
        with patch.object(self.gh, "DB_PATH", db):
            with patch(
                "scripts.generate_hashes.ProcessPoolExecutor",
                return_value=mock_exec,
            ):
                self.gh.generate_hashes_parallel(pending, 1, 0.1, 1)
        out = capsys.readouterr().out
        assert "COMPLETE" in out or "Workers used: 1" in out

    def test_parallel_header_includes_workers(self, tmp_path, capsys):
        db, opus = self._make_db_with_row(tmp_path)
        pending = [(1, str(opus), 0.1, "Book")]
        mock_exec = self._mock_executor()
        with patch.object(self.gh, "DB_PATH", db):
            with patch(
                "scripts.generate_hashes.ProcessPoolExecutor",
                return_value=mock_exec,
            ):
                self.gh.generate_hashes_parallel(pending, 1, 0.1, 2)
        out = capsys.readouterr().out
        assert "Workers: 2" in out


class TestGenerateHashes:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_no_pending_files_exits_early(self, tmp_path, capsys):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, "
                "hash_verified_at TIMESTAMP, title TEXT, file_size_mb REAL)"
            )
            # Insert a row with a hash so "pending" is empty but total > 0
            conn.execute(
                "INSERT INTO audiobooks (id, file_path, sha256_hash, title, file_size_mb) "
                "VALUES (1, '/already.opus', 'abc123', 'Already Hashed', 100.0)"
            )
            conn.commit()
        with patch.object(self.gh, "DB_PATH", db):
            self.gh.generate_hashes()
        out = capsys.readouterr().out
        assert "already have hashes" in out

    def test_parallel_delegates_to_parallel_function(self, tmp_path):
        db = tmp_path / "test.db"
        opus = tmp_path / "book.opus"
        opus.write_bytes(b"data")
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, "
                "hash_verified_at TIMESTAMP, title TEXT, file_size_mb REAL)"
            )
            conn.execute(
                "INSERT INTO audiobooks VALUES (1, ?, NULL, NULL, 'B', 0.1)",
                (str(opus),),
            )
            conn.commit()
        with patch.object(self.gh, "DB_PATH", db):
            with patch.object(
                self.gh, "generate_hashes_parallel"
            ) as mock_par:
                self.gh.generate_hashes(parallel=2)
        mock_par.assert_called_once()

    def test_db_not_found_exits(self, tmp_path):
        missing = tmp_path / "missing.db"
        with patch.object(self.gh, "DB_PATH", missing):
            with pytest.raises(SystemExit):
                self.gh.generate_hashes()


# ---------------------------------------------------------------------------
# _process_parallel_results
# ---------------------------------------------------------------------------


class TestProcessParallelResults:
    def setup_method(self):
        self.gh = _import_generate_hashes()

    def test_processes_successful_future(self, tmp_path, capsys):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.commit()
            cursor = conn.cursor()

            future = MagicMock()
            future.result.return_value = (1, "abc123def456", "My Book", 100.0, None)
            from concurrent.futures import Future

            real_future: Future = Future()
            real_future.set_result((1, "abc123def456", "My Book", 100.0, None))

            processed, processed_size, errors, elapsed = self.gh._process_parallel_results(
                [real_future], 1, 100.0, cursor, conn
            )
        assert processed == 1
        assert errors == 0

    def test_handles_error_result(self, tmp_path, capsys):
        db = tmp_path / "test.db"
        with _db_conn(db) as conn:
            conn.execute(
                "CREATE TABLE audiobooks "
                "(id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT, hash_verified_at TIMESTAMP)"
            )
            conn.commit()
            cursor = conn.cursor()

            from concurrent.futures import Future

            real_future: Future = Future()
            real_future.set_result((2, None, "Bad Book", 50.0, "File not found"))

            processed, processed_size, errors, elapsed = self.gh._process_parallel_results(
                [real_future], 1, 50.0, cursor, conn
            )
        assert processed == 1
        assert errors == 1
