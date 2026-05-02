"""Behavioral tests for maintenance tasks.

These tests verify that each maintenance task produces its claimed effect,
not just that it returns success=True. For example: VACUUM actually changes
the database page count, backup files are restorable, hash mismatches are
accurately reported.
"""

import hashlib
import sqlite3
from pathlib import Path

# ============================================================
# Helpers
# ============================================================


def _create_full_db(db_path: Path, row_count: int = 0) -> Path:
    """Create a database with the audiobooks table and optional rows."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audiobooks ("
        "id INTEGER PRIMARY KEY, "
        "title TEXT, "
        "author TEXT, "
        "file_path TEXT, "
        "sha256_hash TEXT, "
        "hash_verified_at TIMESTAMP"
        ")"
    )
    for i in range(1, row_count + 1):
        conn.execute(
            "INSERT INTO audiobooks (id, title, author, file_path) VALUES (?, ?, ?, ?)",
            (i, f"Title {i}", f"Author {i}", f"/fake/book{i}.opus"),
        )
    conn.commit()
    conn.close()
    return db_path


def _page_count(db_path: Path) -> int:
    """Return the number of pages in a SQLite database file."""
    conn = sqlite3.connect(str(db_path))
    result = conn.execute("PRAGMA page_count").fetchone()[0]
    conn.close()
    return result


def _integrity_check(db_path: Path) -> str:
    """Run PRAGMA integrity_check and return the result string."""
    conn = sqlite3.connect(str(db_path))
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return result


def _make_file(path: Path, content: bytes) -> str:
    """Write content to a file and return its SHA-256 hex digest."""
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


# ============================================================
# VACUUM task — verify actual database effects
# ============================================================


class TestVacuumBehavioral:
    """Verify that VACUUM actually compacts the database."""

    def test_vacuum_reduces_page_count_after_deletes(self, tmp_path):
        """After inserting and deleting many rows, VACUUM should reduce pages."""
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = _create_full_db(tmp_path / "test.db", row_count=500)

        # Delete most rows to create free pages
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM audiobooks WHERE id > 10")
        conn.commit()
        conn.close()

        pages_before = _page_count(db)

        task = DatabaseVacuumTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        pages_after = _page_count(db)
        assert pages_after < pages_before, (
            f"VACUUM should reduce pages: before={pages_before}, after={pages_after}"
        )

    def test_vacuum_preserves_data_integrity(self, tmp_path):
        """After VACUUM, the database should pass integrity check."""
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = _create_full_db(tmp_path / "test.db", row_count=50)

        task = DatabaseVacuumTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        assert _integrity_check(db) == "ok"

    def test_vacuum_preserves_all_data(self, tmp_path):
        """After VACUUM, all rows should still be present."""
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = _create_full_db(tmp_path / "test.db", row_count=100)

        task = DatabaseVacuumTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        conn = sqlite3.connect(str(db))
        count = conn.execute("SELECT COUNT(*) FROM audiobooks").fetchone()[0]
        conn.close()
        assert count == 100

    def test_vacuum_on_empty_database(self, tmp_path):
        """VACUUM on an empty database should succeed without error."""
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        db = _create_full_db(tmp_path / "test.db", row_count=0)

        task = DatabaseVacuumTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True
        assert _integrity_check(db) == "ok"


# ============================================================
# BACKUP task — verify backup contains all data and is restorable
# ============================================================


class TestBackupBehavioral:
    """Verify that backups are complete and can be used to restore data."""

    def test_backup_contains_all_rows(self, tmp_path):
        """Backup database should have the same row count as the original."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_full_db(tmp_path / "test.db", row_count=25)

        # Add data with specific values we can verify
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE audiobooks SET title = 'Special Title' WHERE id = 1")
        conn.commit()
        conn.close()

        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        backup_path = result.data["backup_path"]
        backup_conn = sqlite3.connect(backup_path)
        count = backup_conn.execute("SELECT COUNT(*) FROM audiobooks").fetchone()[0]
        title = backup_conn.execute("SELECT title FROM audiobooks WHERE id = 1").fetchone()[0]
        backup_conn.close()

        assert count == 25
        assert title == "Special Title"

    def test_backup_passes_integrity_check(self, tmp_path):
        """Backup file should pass SQLite integrity check."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_full_db(tmp_path / "test.db", row_count=10)

        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        assert _integrity_check(Path(result.data["backup_path"])) == "ok"

    def test_restore_from_backup_after_corruption(self, tmp_path):
        """After corrupting the original, backup should still have valid data."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_full_db(tmp_path / "test.db", row_count=15)

        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True
        backup_path = Path(result.data["backup_path"])

        # Corrupt the original database by overwriting part of the file
        # (write garbage into the middle of the file)
        original_size = db.stat().st_size
        with open(db, "r+b") as f:
            f.seek(original_size // 2)
            f.write(b"\x00" * 512)

        # Original may or may not pass integrity, but backup must be clean
        assert _integrity_check(backup_path) == "ok"

        # Restore: copy backup over original
        import shutil

        shutil.copy2(backup_path, db)

        # Restored database should work
        conn = sqlite3.connect(str(db))
        count = conn.execute("SELECT COUNT(*) FROM audiobooks").fetchone()[0]
        conn.close()
        assert count == 15

    def test_backup_on_empty_database(self, tmp_path):
        """Backup of an empty database should succeed and produce valid file."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_full_db(tmp_path / "test.db", row_count=0)

        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        backup_conn = sqlite3.connect(result.data["backup_path"])
        count = backup_conn.execute("SELECT COUNT(*) FROM audiobooks").fetchone()[0]
        backup_conn.close()
        assert count == 0

    def test_backup_size_reported_correctly(self, tmp_path):
        """Reported size_mb should match actual file size."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_full_db(tmp_path / "test.db", row_count=50)

        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        actual_mb = Path(result.data["backup_path"]).stat().st_size / (1024 * 1024)
        assert abs(result.data["size_mb"] - actual_mb) < 0.1

    def test_multiple_backups_pruning(self, tmp_path):
        """Creating more than 5 backups should prune the oldest ones."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        db = _create_full_db(tmp_path / "test.db", row_count=5)
        backup_dir = db.parent / "backups"
        backup_dir.mkdir(exist_ok=True)

        # Create 7 backup files manually with distinct timestamps and mtimes
        # to simulate backups created over time (the real task uses timestamps
        # in the filename, and pruning sorts by mtime)

        old_paths = []
        for i in range(7):
            bp = backup_dir / f"test-backup-{i:03d}.db"
            # Create a valid SQLite backup via the backup API
            src = sqlite3.connect(str(db))
            dst = sqlite3.connect(str(bp))
            src.backup(dst)
            src.close()
            dst.close()
            # Set distinct mtimes so pruning can order them
            import os

            os.utime(bp, (1000000 + i, 1000000 + i))
            old_paths.append(bp)

        # Verify we have 7 backups
        assert len(list(backup_dir.glob("*.db"))) == 7

        # Now run the actual backup task, which creates 1 more and prunes
        task = DatabaseBackupTask()
        result = task.execute({"db_path": str(db)})
        assert result.success is True

        # Should have 5 remaining (retention limit): the newest 4 old + 1 new
        remaining = list(backup_dir.glob("*.db"))
        assert len(remaining) == 5

        # The 3 oldest should have been pruned
        assert not old_paths[0].exists()
        assert not old_paths[1].exists()
        assert not old_paths[2].exists()
        # The newest old backup should still exist
        assert old_paths[6].exists()


# ============================================================
# HASH VERIFY task — verify mismatch detection accuracy
# ============================================================


class TestHashVerifyBehavioral:
    """Verify that hash verification correctly detects matches/mismatches."""

    def test_matching_hashes_all_pass(self, tmp_path):
        """Files with correct hashes should all verify successfully."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db")

        # Create real files with known content
        files = []
        conn = sqlite3.connect(str(db))
        for i in range(5):
            content = f"audiobook content {i}".encode()
            fpath = tmp_path / f"book{i}.opus"
            sha = _make_file(fpath, content)
            conn.execute(
                "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (?, ?, ?)",
                (i + 1, str(fpath), sha),
            )
            files.append(fpath)
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True
        assert result.data["verified"] == 5
        assert result.data["total"] == 5
        assert len(result.data["mismatches"]) == 0

    def test_single_corrupted_file_detected(self, tmp_path):
        """Corrupting one file should produce exactly one mismatch."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db")

        conn = sqlite3.connect(str(db))

        # Good file
        good_path = tmp_path / "good.opus"
        good_hash = _make_file(good_path, b"good content")
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            (str(good_path), good_hash),
        )

        # File that will be corrupted
        bad_path = tmp_path / "bad.opus"
        original_hash = _make_file(bad_path, b"original content")
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (2, ?, ?)",
            (str(bad_path), original_hash),
        )

        conn.commit()
        conn.close()

        # Corrupt the file after recording its hash
        bad_path.write_bytes(b"CORRUPTED content")

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is False
        assert result.data["verified"] == 1
        assert len(result.data["mismatches"]) == 1
        assert result.data["mismatches"][0]["id"] == 2
        assert result.data["mismatches"][0]["path"] == str(bad_path)

    def test_mismatch_report_has_correct_ids(self, tmp_path):
        """Mismatch entries should identify the correct audiobook IDs."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db")
        conn = sqlite3.connect(str(db))

        # Create 5 files, corrupt files for IDs 2 and 4
        for i in range(1, 6):
            fpath = tmp_path / f"f{i}.opus"
            sha = _make_file(fpath, f"content-{i}".encode())
            conn.execute(
                "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (?, ?, ?)",
                (i, str(fpath), sha),
            )

        conn.commit()
        conn.close()

        # Corrupt IDs 2 and 4
        (tmp_path / "f2.opus").write_bytes(b"tampered")
        (tmp_path / "f4.opus").write_bytes(b"tampered")

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is False
        mismatch_ids = sorted(m["id"] for m in result.data["mismatches"])
        assert mismatch_ids == [2, 4]
        assert result.data["verified"] == 3

    def test_missing_files_counted_separately(self, tmp_path):
        """Files that do not exist on disk should be counted as missing, not mismatched."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db")
        conn = sqlite3.connect(str(db))

        # Real file
        real_path = tmp_path / "real.opus"
        real_hash = _make_file(real_path, b"real data")
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            (str(real_path), real_hash),
        )

        # Non-existent file
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (2, ?, ?)",
            ("/nonexistent/path.opus", "abc123"),
        )

        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True  # missing files don't cause failure
        assert result.data["verified"] == 1
        assert result.data["missing_count"] == 1
        assert len(result.data["mismatches"]) == 0

    def test_empty_database_no_hashes(self, tmp_path):
        """Database with no hashed files should return success immediately."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db", row_count=5)
        # Rows exist but sha256_hash is NULL

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True
        assert "No files with hashes" in result.message

    def test_large_file_hash_verification(self, tmp_path):
        """Verify that large files (>64KB, multiple chunks) hash correctly."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db")

        # Create a file larger than the 64KB chunk size
        content = b"A" * 200_000  # 200KB
        fpath = tmp_path / "large.opus"
        sha = _make_file(fpath, content)

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            (str(fpath), sha),
        )
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True
        assert result.data["verified"] == 1

    def test_zero_byte_file_hash(self, tmp_path):
        """A zero-byte file should hash correctly (SHA-256 of empty input)."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        db = _create_full_db(tmp_path / "test.db")

        fpath = tmp_path / "empty.opus"
        sha = _make_file(fpath, b"")

        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO audiobooks (id, file_path, sha256_hash) VALUES (1, ?, ?)",
            (str(fpath), sha),
        )
        conn.commit()
        conn.close()

        task = HashVerifyTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True
        assert result.data["verified"] == 1


# ============================================================
# INTEGRITY task — verify actual integrity checking
# ============================================================


class TestIntegrityBehavioral:
    """Verify that integrity check detects actual corruption."""

    def test_healthy_db_passes(self, tmp_path):
        """A well-formed database should pass integrity check."""
        from backend.api_modular.maintenance_tasks.db_integrity import DatabaseIntegrityTask

        db = _create_full_db(tmp_path / "test.db", row_count=20)

        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True
        assert result.data["result"] == "ok"

    def test_corrupted_db_detected(self, tmp_path):
        """Corrupting database bytes should cause integrity check to fail."""
        from backend.api_modular.maintenance_tasks.db_integrity import DatabaseIntegrityTask

        db = _create_full_db(tmp_path / "test.db", row_count=50)
        file_size = db.stat().st_size

        # Corrupt the middle of the database file
        # SQLite header is first 100 bytes; corrupt data pages after that
        with open(db, "r+b") as f:
            f.seek(min(4096, file_size // 2))
            f.write(b"\xff" * 512)

        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is False

    def test_empty_db_passes(self, tmp_path):
        """An empty database (schema only, no rows) should pass."""
        from backend.api_modular.maintenance_tasks.db_integrity import DatabaseIntegrityTask

        db = _create_full_db(tmp_path / "test.db", row_count=0)

        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})

        assert result.success is True
        assert result.data["result"] == "ok"

    def test_integrity_result_includes_database_path(self, tmp_path):
        """Result data should include the database path for audit trail."""
        from backend.api_modular.maintenance_tasks.db_integrity import DatabaseIntegrityTask

        db = _create_full_db(tmp_path / "test.db", row_count=5)

        task = DatabaseIntegrityTask()
        result = task.execute({"db_path": str(db)})

        assert result.data["database"] == str(db)


# ============================================================
# Edge cases — tasks on missing/invalid inputs
# ============================================================


class TestTaskEdgeCases:
    """Edge cases: missing files, corrupt DBs, invalid params."""

    def test_vacuum_on_missing_file(self, tmp_path):
        """VACUUM with a nonexistent db_path should fail gracefully."""
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        task = DatabaseVacuumTask()
        result = task.validate({"db_path": str(tmp_path / "gone.db")})
        assert result.ok is False

    def test_backup_on_missing_file(self, tmp_path):
        """Backup with a nonexistent db_path should fail gracefully."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask

        task = DatabaseBackupTask()
        result = task.validate({"db_path": str(tmp_path / "gone.db")})
        assert result.ok is False

    def test_hash_verify_on_corrupt_db(self, tmp_path):
        """Hash verify on a corrupt database file should fail gracefully."""
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        bad_db = tmp_path / "bad.db"
        bad_db.write_text("this is not a database")

        task = HashVerifyTask()
        result = task.execute({"db_path": str(bad_db)})
        assert result.success is False

    def test_vacuum_on_corrupt_db(self, tmp_path):
        """VACUUM on a corrupt database file should fail gracefully."""
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask

        bad_db = tmp_path / "bad.db"
        bad_db.write_text("this is not a database")

        task = DatabaseVacuumTask()
        result = task.execute({"db_path": str(bad_db)})
        assert result.success is False

    def test_all_tasks_handle_no_db_path(self):
        """All tasks should fail gracefully when no db_path is provided."""
        from backend.api_modular.maintenance_tasks.db_backup import DatabaseBackupTask
        from backend.api_modular.maintenance_tasks.db_integrity import DatabaseIntegrityTask
        from backend.api_modular.maintenance_tasks.db_vacuum import DatabaseVacuumTask
        from backend.api_modular.maintenance_tasks.hash_verify import HashVerifyTask

        for TaskClass in [
            DatabaseVacuumTask,
            DatabaseBackupTask,
            DatabaseIntegrityTask,
            HashVerifyTask,
        ]:
            task = TaskClass()
            result = task.execute({})
            assert result.success is False, f"{TaskClass.name} should fail without db_path"
