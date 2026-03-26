"""
Tests for maintenance task modules: base, db_backup, db_integrity, db_vacuum,
hash_verify, library_scan, and the registry (__init__).

All database and filesystem operations are isolated via tmp_path / mocks.
"""

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.api_modular.maintenance_tasks.base import (
    ExecutionResult,
    MaintenanceRegistry,
    MaintenanceTask,
    ValidationResult,
)


# ============================================================
# Helper: create a minimal SQLite DB for task tests
# ============================================================

def _create_test_db(db_path: Path) -> Path:
    """Create a minimal audiobook database at db_path."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audiobooks ("
        "id INTEGER PRIMARY KEY, file_path TEXT, sha256_hash TEXT)"
    )
    conn.close()
    return db_path


# ============================================================
# base.py — dataclasses
# ============================================================


class TestValidationResult:
    def test_ok_result(self):
        r = ValidationResult(ok=True)
        assert r.ok is True
        assert r.message == ""

    def test_ok_with_message(self):
        r = ValidationResult(ok=True, message="all good")
        assert r.ok is True
        assert r.message == "all good"

    def test_fail_result(self):
        r = ValidationResult(ok=False, message="missing db")
        assert r.ok is False
        assert r.message == "missing db"


class TestExecutionResult:
    def test_success_result(self):
        r = ExecutionResult(success=True, message="done")
        assert r.success is True
        assert r.data == {}

    def test_success_with_data(self):
        r = ExecutionResult(success=True, message="ok", data={"key": 1})
        assert r.data == {"key": 1}

    def test_failure_result(self):
        r = ExecutionResult(success=False, message="boom")
        assert r.success is False


# ============================================================
# base.py — MaintenanceTask ABC
# ============================================================


class _DummyTask(MaintenanceTask):
    name = "dummy"
    display_name = "Dummy Task"
    description = "A dummy task for testing"

    def validate(self, params):
        return ValidationResult(ok=True)

    def execute(self, params, progress_callback=None):
        return ExecutionResult(success=True, message="ran")


class _NoNameTask(MaintenanceTask):
    # name deliberately empty
    def validate(self, params):
        return ValidationResult(ok=True)

    def execute(self, params, progress_callback=None):
        return ExecutionResult(success=True)


class TestMaintenanceTaskABC:
    def test_to_dict(self):
        t = _DummyTask()
        d = t.to_dict()
        assert d["name"] == "dummy"
        assert d["display_name"] == "Dummy Task"
        assert d["description"] == "A dummy task for testing"
        assert d["estimated_duration"] is None

    def test_estimate_duration_default_none(self):
        assert _DummyTask().estimate_duration() is None


# ============================================================
# base.py — MaintenanceRegistry
# ============================================================


class TestMaintenanceRegistry:
    def test_register_and_get(self):
        reg = MaintenanceRegistry()
        reg.register(_DummyTask)
        task = reg.get("dummy")
        assert task is not None
        assert task.name == "dummy"

    def test_register_no_name_raises(self):
        reg = MaintenanceRegistry()
        with pytest.raises(ValueError, match="must define a 'name'"):
            reg.register(_NoNameTask)

    def test_get_unknown_returns_none(self):
        reg = MaintenanceRegistry()
        assert reg.get("nonexistent") is None

    def test_list_all(self):
        reg = MaintenanceRegistry()
        reg.register(_DummyTask)
        items = reg.list_all()
        assert len(items) == 1
        assert items[0]["name"] == "dummy"

    def test_register_returns_class(self):
        reg = MaintenanceRegistry()
        result = reg.register(_DummyTask)
        assert result is _DummyTask


# ============================================================
# __init__.py — singleton registry auto-discovery
# ============================================================


class TestRegistryAutoDiscovery:
    def test_registry_has_known_tasks(self):
        from backend.api_modular.maintenance_tasks import registry

        known = ["db_vacuum", "db_backup", "db_integrity", "hash_verify", "library_scan"]
        for name in known:
            assert registry.get(name) is not None, f"Task '{name}' not registered"

    def test_registry_list_all_returns_dicts(self):
        from backend.api_modular.maintenance_tasks import registry

        items = registry.list_all()
        assert isinstance(items, list)
        assert len(items) >= 5
        for item in items:
            assert "name" in item
            assert "display_name" in item


# ============================================================
# db_vacuum.py — _resolve_db_path + DatabaseVacuumTask
# ============================================================


class TestResolveDbPath:
    def test_from_params(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_vacuum import _resolve_db_path

        db = tmp_path / "test.db"
        db.touch()
        result = _resolve_db_path({"db_path": str(db)})
        assert result == Path(str(db))

    def test_from_flask_context(self, flask_app):
        from backend.api_modular.maintenance_tasks.db_vacuum import _resolve_db_path

        with flask_app.app_context():
            result = _resolve_db_path({})
            assert result is not None

    def test_no_context_returns_none(self):
        from backend.api_modular.maintenance_tasks.db_vacuum import _resolve_db_path

        # Outside Flask context and no db_path param -> returns None
        result = _resolve_db_path({})
        assert result is None


class TestDatabaseVacuumTask:
    def test_validate_ok(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseVacuumTask()
        result = task.validate({"db_path": str(db)})
        assert result.ok is True

    def test_validate_missing_db(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        task = DatabaseVacuumTask()
        result = task.validate({"db_path": str(tmp_path / "nonexistent.db")})
        assert result.ok is False

    def test_execute_success(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseVacuumTask()
        cb = MagicMock()
        result = task.execute({"db_path": str(db)}, progress_callback=cb)
        assert result.success is True
        assert "VACUUM" in result.message
        assert cb.call_count >= 2  # at least ANALYZE + VACUUM callbacks

    def test_execute_no_db_path(self):
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        task = DatabaseVacuumTask()
        # Outside Flask context with no db_path param
        result = task.execute({})
        assert result.success is False
        assert "not available" in result.message

    def test_execute_db_error(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = tmp_path / "test.db"
        db.write_text("not a database")
        task = DatabaseVacuumTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is False

    def test_estimate_duration(self):
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        assert DatabaseVacuumTask().estimate_duration() == 30


# ============================================================
# db_backup.py — DatabaseBackupTask
# ============================================================


class TestDatabaseBackupTask:
    def test_validate_ok(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseBackupTask()
        result = task.validate({"db_path": str(db)})
        assert result.ok is True

    def test_validate_missing_db(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        task = DatabaseBackupTask()
        result = task.validate({"db_path": str(tmp_path / "missing.db")})
        assert result.ok is False
        assert "not found" in result.message

    def test_execute_creates_backup(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_test_db(tmp_path / "test.db")
        # Insert some data to make the backup non-trivial
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO audiobooks (file_path) VALUES ('/test/file.opus')")
        conn.commit()
        conn.close()

        task = DatabaseBackupTask()
        cb = MagicMock()
        result = task.execute({"db_path": str(db)}, progress_callback=cb)
        assert result.success is True
        assert "Backup created" in result.message
        assert "backup_path" in result.data
        assert "size_mb" in result.data

        # Verify backup file exists
        backup_path = Path(result.data["backup_path"])
        assert backup_path.exists()
        assert backup_path.parent.name == "backups"

    def test_execute_backup_dir_created(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True
        assert (tmp_path / "backups").is_dir()

    def test_execute_no_db_path(self):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        task = DatabaseBackupTask()
        result = task.execute({})
        assert result.success is False
        assert "not available" in result.message

    def test_execute_handles_sqlite_error(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = tmp_path / "bad.db"
        db.write_text("not a database")
        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is False

    def test_execute_progress_callback_called(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_test_db(tmp_path / "test.db")
        cb = MagicMock()
        task = DatabaseBackupTask()
        task.execute({"db_path": str(db)}, progress_callback=cb)
        # Should call at 0.2 ("Creating backup...") and 1.0 ("Complete")
        assert cb.call_count == 2
        cb.assert_any_call(0.2, "Creating backup...")
        cb.assert_any_call(1.0, "Complete")

    def test_estimate_duration(self):
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        assert DatabaseBackupTask().estimate_duration() == 30

    def test_backup_is_valid_sqlite(self, tmp_path):
        """Verify the backup is a valid SQLite database with correct data."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_test_db(tmp_path / "test.db")
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO audiobooks (file_path) VALUES ('/test/book.opus')")
        conn.commit()
        conn.close()

        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        backup_conn = sqlite3.connect(result.data["backup_path"])
        rows = backup_conn.execute("SELECT file_path FROM audiobooks").fetchall()
        backup_conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "/test/book.opus"


# ============================================================
# db_integrity.py — DatabaseIntegrityTask
# ============================================================


class TestDatabaseIntegrityTask:
    def test_validate_ok(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseIntegrityTask()
        result = task.validate({"db_path": str(db)})
        assert result.ok is True

    def test_validate_missing_db(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        task = DatabaseIntegrityTask()
        result = task.validate({"db_path": str(tmp_path / "missing.db")})
        assert result.ok is False

    def test_execute_healthy_db(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseIntegrityTask()
        cb = MagicMock()
        result = task.execute({"db_path": str(db)}, progress_callback=cb)
        assert result.success is True
        assert result.data["result"] == "ok"
        assert cb.call_count >= 2

    def test_execute_no_db_path(self):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        task = DatabaseIntegrityTask()
        result = task.execute({})
        assert result.success is False

    def test_execute_corrupted_db(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        db = tmp_path / "bad.db"
        db.write_text("not a real database file contents")
        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is False

    def test_execute_progress_callback(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        db = _create_test_db(tmp_path / "test.db")
        cb = MagicMock()
        task = DatabaseIntegrityTask()
        task.execute({"db_path": str(db)}, progress_callback=cb)
        cb.assert_any_call(0.3, "Running integrity check...")
        cb.assert_any_call(1.0, "Complete")

    def test_execute_without_callback(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

    def test_estimate_duration(self):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        assert DatabaseIntegrityTask().estimate_duration() == 60

    def test_data_includes_database_path(self, tmp_path):
        from backend.api_modular.maintenance_tasks.db_integrity import (
            DatabaseIntegrityTask,
        )

        db = _create_test_db(tmp_path / "test.db")
        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})
        assert result.data["database"] == str(db)


# ============================================================
# hash_verify.py — HashVerifyTask
# ============================================================


class TestHashVerifyTask:
    def _make_file_and_hash(self, path: Path, content: bytes) -> str:
        """Write content to path and return SHA-256 hex digest."""
        path.write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    def test_validate_ok(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        task = HashVerifyTask()
        assert task.validate({"db_path": str(db)}).ok is True

    def test_validate_missing_db(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        task = HashVerifyTask()
        assert task.validate({"db_path": str(tmp_path / "nope.db")}).ok is False

    def test_execute_no_hashes(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True
        assert "No files with hashes" in result.message

    def test_execute_all_match(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        f1 = tmp_path / "book1.opus"
        h1 = self._make_file_and_hash(f1, b"audiobook content 1")
        f2 = tmp_path / "book2.opus"
        h2 = self._make_file_and_hash(f2, b"audiobook content 2")

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            (str(f1), h1),
        )
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (2, ?, ?)",
            (str(f2), h2),
        )
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        cb = MagicMock()
        result = task.execute({"db_path": str(db)}, progress_callback=cb)
        assert result.success is True
        assert result.data["verified"] == 2
        assert result.data["total"] == 2
        assert len(result.data["mismatches"]) == 0

    def test_execute_mismatch_detected(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        f1 = tmp_path / "book.opus"
        f1.write_bytes(b"actual content")

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            (str(f1), "0000000000000000000000000000000000000000000000000000000000000000"),
        )
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is False
        assert len(result.data["mismatches"]) == 1
        assert result.data["mismatches"][0]["id"] == 1

    def test_execute_missing_file(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            ("/nonexistent/path/book.opus", "abc123"),
        )
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True  # missing files don't cause failure
        assert result.data["missing_count"] == 1
        assert result.data["verified"] == 0

    def test_execute_progress_callback_intervals(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        conn = sqlite3.connect(str(db))
        # Create 25 entries so progress_callback fires multiple times (every 10)
        for i in range(25):
            fp = tmp_path / f"b{i}.opus"
            fp.write_bytes(f"content{i}".encode())
            h = hashlib.sha256(f"content{i}".encode()).hexdigest()
            conn.execute(
                "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (?, ?, ?)",
                (i + 1, str(fp), h),
            )
        conn.commit()
        conn.close()

        cb = MagicMock()
        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)}, progress_callback=cb)
        assert result.success is True
        # Progress calls at i=0,10,20 plus final 1.0
        assert cb.call_count >= 3

    def test_execute_no_db_path(self):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        task = HashVerifyTask()
        result = task.execute({})
        assert result.success is False

    def test_execute_db_error(self, tmp_path):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = tmp_path / "bad.db"
        db.write_text("corrupt")
        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is False

    def test_estimate_duration(self):
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        assert HashVerifyTask().estimate_duration() == 600

    def test_mismatches_truncated_to_20(self, tmp_path):
        """Verify that mismatches list is capped at 20 entries."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_test_db(tmp_path / "test.db")
        conn = sqlite3.connect(str(db))
        for i in range(30):
            fp = tmp_path / f"f{i}.opus"
            fp.write_bytes(f"content{i}".encode())
            conn.execute(
                "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (?, ?, ?)",
                (i + 1, str(fp), "badhash"),
            )
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is False
        assert len(result.data["mismatches"]) == 20  # capped


# ============================================================
# library_scan.py — LibraryScanTask
# ============================================================


class TestLibraryScanTask:
    def test_validate_always_ok(self):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        task = LibraryScanTask()
        result = task.validate({})
        assert result.ok is True

    @patch("subprocess.run")
    def test_execute_success(self, mock_run):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        mock_run.return_value = MagicMock(returncode=0, stdout='{"status":"ok"}', stderr="")
        task = LibraryScanTask()
        cb = MagicMock()
        result = task.execute({}, progress_callback=cb)
        assert result.success is True
        assert "scan completed" in result.message
        assert cb.call_count == 2

    @patch("subprocess.run")
    def test_execute_failure(self, mock_run):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="curl error")
        task = LibraryScanTask()
        result = task.execute({})
        assert result.success is False
        assert "failed" in result.message.lower()

    @patch("subprocess.run")
    def test_execute_timeout(self, mock_run):
        import subprocess

        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="curl", timeout=600)
        task = LibraryScanTask()
        result = task.execute({})
        assert result.success is False
        assert "timed out" in result.message

    @patch("subprocess.run")
    def test_execute_exception(self, mock_run):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        mock_run.side_effect = OSError("connection refused")
        task = LibraryScanTask()
        result = task.execute({})
        assert result.success is False
        assert "connection refused" in result.message

    @patch("subprocess.run")
    def test_execute_without_callback(self, mock_run):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        task = LibraryScanTask()
        result = task.execute({}, progress_callback=None)
        assert result.success is True

    @patch("subprocess.run")
    def test_output_truncated_in_data(self, mock_run):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        mock_run.return_value = MagicMock(returncode=0, stdout="x" * 1000, stderr="")
        task = LibraryScanTask()
        result = task.execute({})
        assert len(result.data["output"]) == 500

    def test_estimate_duration(self):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        assert LibraryScanTask().estimate_duration() == 300

    def test_task_attributes(self):
        from backend.api_modular.maintenance_tasks.library_scan import LibraryScanTask

        task = LibraryScanTask()
        assert task.name == "library_scan"
        assert task.display_name == "Library Rescan"
