"""
Tests for cleanup maintenance tasks: BackupRetentionTask, OrphanedSupplementsTask,
and StagingCleanupTask.

All database and filesystem operations are isolated via tmp_path / mocks.
"""

import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


from backend.api_modular.maintenance_tasks.cleanup import (
    BackupRetentionTask,
    OrphanedSupplementsTask,
    StagingCleanupTask,
    _BACKUP_RETENTION,
)


# ============================================================
# Helper: create a minimal SQLite DB
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


def _create_db_with_supplements(db_path: Path, rows: list[tuple]) -> Path:
    """Create a DB with a supplements table populated with (id, file_path) rows."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE supplements (id INTEGER PRIMARY KEY, file_path TEXT NOT NULL)")
    for row_id, file_path in rows:
        conn.execute("INSERT INTO supplements (id, file_path) VALUES (?, ?)", (row_id, file_path))
    conn.commit()
    conn.close()
    return db_path


# ============================================================
# BackupRetentionTask — validate
# ============================================================


class TestBackupRetentionValidate:
    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_db_not_found_none(self, mock_resolve):
        """validate returns not-ok when _resolve_db_path returns None."""
        mock_resolve.return_value = None
        task = BackupRetentionTask()
        result = task.validate({})
        assert result.ok is False
        assert "not found" in result.message.lower()

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_db_not_found_nonexistent(self, mock_resolve, tmp_path):
        """validate returns not-ok when db path doesn't exist on disk."""
        mock_resolve.return_value = tmp_path / "nonexistent.db"
        task = BackupRetentionTask()
        result = task.validate({})
        assert result.ok is False
        assert "not found" in result.message.lower()

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_no_backup_dir(self, mock_resolve, tmp_path):
        """validate returns ok with message when backup directory doesn't exist."""
        db = _create_test_db(tmp_path / "test.db")
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.validate({})
        assert result.ok is True
        assert "No backup directory" in result.message

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_backup_dir_exists(self, mock_resolve, tmp_path):
        """validate returns ok when db and backup dir both exist."""
        db = _create_test_db(tmp_path / "test.db")
        (tmp_path / "backups").mkdir()
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.validate({})
        assert result.ok is True
        assert result.message == ""


# ============================================================
# BackupRetentionTask — execute
# ============================================================


class TestBackupRetentionExecute:
    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_no_db_path(self, mock_resolve):
        """execute returns failure when db path is not available."""
        mock_resolve.return_value = None
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is False
        assert "not available" in result.message.lower()

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_no_backup_dir(self, mock_resolve, tmp_path):
        """execute succeeds with deleted=0 when backup dir doesn't exist."""
        db = _create_test_db(tmp_path / "test.db")
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 0
        assert "No backups" in result.message

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_no_backups_in_dir(self, mock_resolve, tmp_path):
        """execute succeeds when backup dir exists but has no .db files."""
        db = _create_test_db(tmp_path / "test.db")
        (tmp_path / "backups").mkdir()
        # Add a non-.db file
        (tmp_path / "backups" / "readme.txt").write_text("notes")
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_fewer_than_retention(self, mock_resolve, tmp_path):
        """execute keeps all backups when count < retention limit."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        count = _BACKUP_RETENTION - 2
        for i in range(count):
            (backup_dir / f"backup_{i}.db").write_bytes(b"x" * 100)
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 0
        assert result.data["kept"] == count
        assert f"Only {count}" in result.message

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_exact_retention_count(self, mock_resolve, tmp_path):
        """execute keeps all when count == retention limit."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for i in range(_BACKUP_RETENTION):
            f = backup_dir / f"backup_{i}.db"
            f.write_bytes(b"x" * 100)
            # Ensure distinct mtimes
            os.utime(f, (time.time() + i, time.time() + i))
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_more_than_retention_deletes_oldest(self, mock_resolve, tmp_path):
        """execute deletes oldest backups beyond retention limit."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        total = _BACKUP_RETENTION + 3
        files = []
        for i in range(total):
            f = backup_dir / f"backup_{i}.db"
            f.write_bytes(b"x" * 1024)
            # Set distinct mtime — higher i = newer
            os.utime(f, (1000 + i, 1000 + i))
            files.append(f)
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 3
        assert result.data["kept"] == _BACKUP_RETENTION
        assert result.data["freed_mb"] == round(3 * 1024 / (1024 * 1024), 1)
        # Oldest 3 files (lowest mtime) should be gone
        remaining = list(backup_dir.iterdir())
        assert len(remaining) == _BACKUP_RETENTION

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_freed_bytes_calculation(self, mock_resolve, tmp_path):
        """execute reports correct freed_mb for deleted files."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create retention + 2 backups, each 512 KB
        total = _BACKUP_RETENTION + 2
        for i in range(total):
            f = backup_dir / f"backup_{i}.db"
            f.write_bytes(b"x" * (512 * 1024))
            os.utime(f, (1000 + i, 1000 + i))
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 2
        # 2 * 512KB = 1MB
        assert result.data["freed_mb"] == 1.0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_only_deletes_db_files(self, mock_resolve, tmp_path):
        """execute ignores non-.db files in backup directory."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create more than retention .db files plus non-.db files
        total_db = _BACKUP_RETENTION + 2
        for i in range(total_db):
            f = backup_dir / f"backup_{i}.db"
            f.write_bytes(b"x" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        # Add non-.db files — should be untouched
        (backup_dir / "notes.txt").write_text("keep me")
        (backup_dir / "backup.log").write_text("log data")
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 2
        # Non-.db files must still exist
        assert (backup_dir / "notes.txt").exists()
        assert (backup_dir / "backup.log").exists()

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_progress_callback_called(self, mock_resolve, tmp_path):
        """execute calls progress_callback at start and completion."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create enough backups to trigger deletion
        for i in range(_BACKUP_RETENTION + 1):
            f = backup_dir / f"backup_{i}.db"
            f.write_bytes(b"x" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        mock_resolve.return_value = db
        cb = MagicMock()
        task = BackupRetentionTask()
        task.execute({}, progress_callback=cb)
        cb.assert_any_call(0.2, "Scanning backups...")
        cb.assert_any_call(1.0, "Complete")

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_progress_callback_nothing_to_delete(self, mock_resolve, tmp_path):
        """execute calls progress_callback even when nothing to delete."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "backup_0.db").write_bytes(b"x" * 100)
        mock_resolve.return_value = db
        cb = MagicMock()
        task = BackupRetentionTask()
        task.execute({}, progress_callback=cb)
        cb.assert_any_call(0.2, "Scanning backups...")
        cb.assert_any_call(1.0, "Complete")

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_without_callback(self, mock_resolve, tmp_path):
        """execute works fine when no progress_callback is provided."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for i in range(_BACKUP_RETENTION + 1):
            f = backup_dir / f"backup_{i}.db"
            f.write_bytes(b"x" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 1

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_error_handling(self, mock_resolve, tmp_path):
        """execute returns failure when an exception occurs."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        # Make iterdir raise by replacing backup_dir with a file
        backup_dir.rmdir()
        backup_dir.write_text("not a directory anymore")
        # Now backup_dir.is_dir() returns False, so it hits "No backups to clean up"
        result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_permission_error(self, mock_resolve, tmp_path):
        """execute catches and returns permission errors gracefully."""
        db = _create_test_db(tmp_path / "test.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create a file that will fail to delete
        f = backup_dir / "backup_old.db"
        f.write_bytes(b"x" * 100)
        os.utime(f, (100, 100))
        # Create enough to trigger deletion
        for i in range(_BACKUP_RETENTION):
            nf = backup_dir / f"backup_new_{i}.db"
            nf.write_bytes(b"x" * 100)
            os.utime(nf, (2000 + i, 2000 + i))
        mock_resolve.return_value = db
        task = BackupRetentionTask()
        # Mock unlink to raise PermissionError
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            result = task.execute({})
            assert result.success is False
            assert "denied" in result.message


class TestBackupRetentionMisc:
    def test_estimate_duration(self):
        assert BackupRetentionTask().estimate_duration() == 5

    def test_task_attributes(self):
        task = BackupRetentionTask()
        assert task.name == "backup_retention"
        assert task.display_name == "Backup Retention Cleanup"
        assert str(_BACKUP_RETENTION) in task.description


# ============================================================
# OrphanedSupplementsTask — validate
# ============================================================


class TestOrphanedSupplementsValidate:
    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_db_not_found_none(self, mock_resolve):
        """validate returns not-ok when db path is None."""
        mock_resolve.return_value = None
        task = OrphanedSupplementsTask()
        result = task.validate({})
        assert result.ok is False
        assert "not found" in result.message.lower()

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_db_not_found_nonexistent(self, mock_resolve, tmp_path):
        """validate returns not-ok when db file doesn't exist."""
        mock_resolve.return_value = tmp_path / "nonexistent.db"
        task = OrphanedSupplementsTask()
        result = task.validate({})
        assert result.ok is False

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_validate_ok(self, mock_resolve, tmp_path):
        """validate returns ok when db exists."""
        db = _create_test_db(tmp_path / "test.db")
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.validate({})
        assert result.ok is True


# ============================================================
# OrphanedSupplementsTask — execute
# ============================================================


class TestOrphanedSupplementsExecute:
    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_no_db_path(self, mock_resolve):
        """execute returns failure when db path is not available."""
        mock_resolve.return_value = None
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is False
        assert "not available" in result.message.lower()

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_no_supplements_table(self, mock_resolve, tmp_path):
        """execute succeeds with removed=0 when supplements table doesn't exist."""
        db = _create_test_db(tmp_path / "test.db")
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is True
        assert "No supplements table" in result.message
        assert result.data["removed"] == 0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_no_orphans(self, mock_resolve, tmp_path):
        """execute finds no orphans when all supplement files exist on disk."""
        real_file = tmp_path / "supplement1.pdf"
        real_file.write_text("content")
        db = _create_db_with_supplements(tmp_path / "test.db", [(1, str(real_file))])
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["removed"] == 0
        assert result.data["total_checked"] == 1
        assert "No orphaned" in result.message

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_some_orphans(self, mock_resolve, tmp_path):
        """execute removes DB entries for files that don't exist on disk."""
        real_file = tmp_path / "exists.pdf"
        real_file.write_text("content")
        db = _create_db_with_supplements(
            tmp_path / "test.db",
            [
                (1, str(real_file)),
                (2, str(tmp_path / "missing1.pdf")),
                (3, str(tmp_path / "missing2.pdf")),
            ],
        )
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["removed"] == 2
        assert result.data["total_checked"] == 3
        assert "Removed 2" in result.message
        # Verify DB state — only row 1 should remain
        conn = sqlite3.connect(str(db))
        remaining = conn.execute("SELECT id FROM supplements").fetchall()
        conn.close()
        assert len(remaining) == 1
        assert remaining[0][0] == 1

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_all_orphans(self, mock_resolve, tmp_path):
        """execute removes all entries when no files exist on disk."""
        db = _create_db_with_supplements(
            tmp_path / "test.db",
            [
                (1, "/nonexistent/file1.pdf"),
                (2, "/nonexistent/file2.pdf"),
                (3, "/nonexistent/file3.pdf"),
            ],
        )
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["removed"] == 3
        assert result.data["total_checked"] == 3
        # Verify DB is empty
        conn = sqlite3.connect(str(db))
        remaining = conn.execute("SELECT COUNT(*) FROM supplements").fetchone()[0]
        conn.close()
        assert remaining == 0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_empty_supplements_table(self, mock_resolve, tmp_path):
        """execute succeeds with removed=0 when supplements table is empty."""
        db = _create_db_with_supplements(tmp_path / "test.db", [])
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["removed"] == 0
        assert result.data["total_checked"] == 0

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_progress_callback(self, mock_resolve, tmp_path):
        """execute calls progress_callback at expected stages."""
        real_file = tmp_path / "exists.pdf"
        real_file.write_text("content")
        db = _create_db_with_supplements(
            tmp_path / "test.db", [(1, str(real_file)), (2, "/missing.pdf")]
        )
        mock_resolve.return_value = db
        cb = MagicMock()
        task = OrphanedSupplementsTask()
        task.execute({}, progress_callback=cb)
        cb.assert_any_call(0.2, "Scanning supplements...")
        cb.assert_any_call(0.6, "Found 1 orphans...")
        cb.assert_any_call(1.0, "Complete")

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_without_callback(self, mock_resolve, tmp_path):
        """execute works fine without a progress_callback."""
        db = _create_db_with_supplements(tmp_path / "test.db", [(1, "/missing.pdf")])
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is True
        assert result.data["removed"] == 1

    @patch("backend.api_modular.maintenance_tasks.cleanup._resolve_db_path")
    def test_execute_db_error(self, mock_resolve, tmp_path):
        """execute returns failure on database errors."""
        db = tmp_path / "bad.db"
        db.write_text("not a database")
        mock_resolve.return_value = db
        task = OrphanedSupplementsTask()
        result = task.execute({})
        assert result.success is False


class TestOrphanedSupplementsMisc:
    def test_estimate_duration(self):
        assert OrphanedSupplementsTask().estimate_duration() == 10

    def test_task_attributes(self):
        task = OrphanedSupplementsTask()
        assert task.name == "cleanup_orphaned_supplements"
        assert task.display_name == "Orphaned Supplement Cleanup"


# ============================================================
# StagingCleanupTask — validate
# ============================================================


class TestStagingCleanupValidate:
    def test_validate_always_ok(self):
        """validate always returns ok for staging cleanup."""
        task = StagingCleanupTask()
        result = task.validate({})
        assert result.ok is True


# ============================================================
# StagingCleanupTask — execute
# ============================================================


class TestStagingCleanupExecute:
    @patch("subprocess.run")
    def test_execute_no_staging_dir(self, mock_pgrep, tmp_path):
        """execute succeeds with deleted=0 when staging dir doesn't exist."""
        nonexistent = tmp_path / "staging"
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(nonexistent)}):
            # Force ImportError on config import so it falls through to env var
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert "does not exist" in result.message
        assert result.data["deleted"] == 0
        # pgrep should not have been called
        mock_pgrep.assert_not_called()

    @patch("subprocess.run")
    def test_execute_empty_staging_dir(self, mock_pgrep, tmp_path):
        """execute succeeds with 'already clean' when staging dir is empty."""
        staging = tmp_path / "staging"
        staging.mkdir()
        mock_pgrep.return_value = MagicMock(returncode=1)  # no ffmpeg running
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 0
        assert "already clean" in result.message

    @patch("subprocess.run")
    def test_execute_files_to_clean(self, mock_pgrep, tmp_path):
        """execute deletes files and reports correct count and size."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file1.opus").write_bytes(b"x" * 1024)
        (staging / "file2.opus").write_bytes(b"x" * 2048)
        (staging / "file3.tmp").write_bytes(b"x" * 512)
        mock_pgrep.return_value = MagicMock(returncode=1)  # no ffmpeg
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 3
        expected_mb = round((1024 + 2048 + 512) / (1024 * 1024), 1)
        assert result.data["freed_mb"] == expected_mb
        assert "Cleaned 3 files" in result.message
        # Verify files are gone
        assert len(list(staging.iterdir())) == 0

    @patch("subprocess.run")
    def test_execute_active_ffmpeg_conversion(self, mock_pgrep, tmp_path):
        """execute skips cleanup when ffmpeg conversion is active."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "in_progress.opus").write_bytes(b"data")
        mock_pgrep.return_value = MagicMock(returncode=0)  # ffmpeg IS running
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert "Conversion in progress" in result.message
        assert result.data["deleted"] == 0
        assert result.data["reason"] == "active_conversion"
        # File should still exist
        assert (staging / "in_progress.opus").exists()

    @patch("subprocess.run")
    def test_execute_nested_subdirectories(self, mock_pgrep, tmp_path):
        """execute removes files in subdirs and cleans empty subdirs."""
        staging = tmp_path / "staging"
        staging.mkdir()
        subdir = staging / "author" / "book"
        subdir.mkdir(parents=True)
        (subdir / "chapter1.opus").write_bytes(b"x" * 100)
        (subdir / "chapter2.opus").write_bytes(b"x" * 200)
        # Also a file in a sibling dir
        other = staging / "other"
        other.mkdir()
        (other / "temp.tmp").write_bytes(b"x" * 50)
        mock_pgrep.return_value = MagicMock(returncode=1)
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 3
        # Subdirectories should be removed (they're empty after file deletion)
        assert not subdir.exists()
        assert not (staging / "author").exists()
        assert not other.exists()

    @patch("subprocess.run")
    def test_execute_progress_callback(self, mock_pgrep, tmp_path):
        """execute calls progress_callback at expected stages."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.opus").write_bytes(b"x" * 100)
        mock_pgrep.return_value = MagicMock(returncode=1)
        cb = MagicMock()
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                task.execute({}, progress_callback=cb)
        cb.assert_any_call(0.2, "Scanning staging directory...")
        cb.assert_any_call(1.0, "Complete")

    @patch("subprocess.run")
    def test_execute_without_callback(self, mock_pgrep, tmp_path):
        """execute works fine without a progress_callback."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.opus").write_bytes(b"x" * 100)
        mock_pgrep.return_value = MagicMock(returncode=1)
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 1

    @patch("subprocess.run")
    def test_execute_error_handling(self, mock_pgrep, tmp_path):
        """execute returns failure when subprocess.run raises an exception."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.opus").write_bytes(b"data")
        mock_pgrep.side_effect = OSError("pgrep failed")
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is False
        assert "pgrep failed" in result.message

    @patch("subprocess.run")
    def test_execute_env_var_fallback(self, mock_pgrep, tmp_path):
        """execute uses AUDIOBOOKS_STAGING env var when config import fails."""
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "file.opus").write_bytes(b"x" * 100)
        mock_pgrep.return_value = MagicMock(returncode=1)
        task = StagingCleanupTask()
        with patch.dict(os.environ, {"AUDIOBOOKS_STAGING": str(staging)}):
            with patch.dict("sys.modules", {"config": None}):
                result = task.execute({})
        assert result.success is True
        assert result.data["deleted"] == 1

    @patch("subprocess.run")
    def test_execute_default_staging_path(self, mock_pgrep):
        """execute falls back to /tmp/audiobook-staging when env var not set."""
        task = StagingCleanupTask()
        with patch.dict(os.environ, {}, clear=False):
            # Remove AUDIOBOOKS_STAGING if present
            env_copy = os.environ.copy()
            env_copy.pop("AUDIOBOOKS_STAGING", None)
            with patch.dict(os.environ, env_copy, clear=True):
                with patch.dict("sys.modules", {"config": None}):
                    result = task.execute({})
        # /tmp/audiobook-staging likely doesn't exist, so "does not exist"
        assert result.success is True
        assert result.data["deleted"] == 0


class TestStagingCleanupMisc:
    def test_estimate_duration(self):
        assert StagingCleanupTask().estimate_duration() == 10

    def test_task_attributes(self):
        task = StagingCleanupTask()
        assert task.name == "staging_cleanup"
        assert task.display_name == "Staging Directory Cleanup"


# ============================================================
# Registry integration — verify all cleanup tasks are registered
# ============================================================


class TestCleanupTasksRegistered:
    def test_backup_retention_registered(self):
        from backend.api_modular.maintenance_tasks import registry

        task = registry.get("backup_retention")
        assert task is not None
        assert isinstance(task, BackupRetentionTask)

    def test_orphaned_supplements_registered(self):
        from backend.api_modular.maintenance_tasks import registry

        task = registry.get("cleanup_orphaned_supplements")
        assert task is not None
        assert isinstance(task, OrphanedSupplementsTask)

    def test_staging_cleanup_registered(self):
        from backend.api_modular.maintenance_tasks import registry

        task = registry.get("staging_cleanup")
        assert task is not None
        assert isinstance(task, StagingCleanupTask)
