"""Cleanup tasks for orphaned and expired data."""

import logging
import sqlite3
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)

# Maximum backups to retain
_BACKUP_RETENTION = 5


@registry.register
class BackupRetentionTask(MaintenanceTask):
    name = "backup_retention"
    display_name = "Backup Retention Cleanup"
    description = (
        f"Remove old database backups, keeping the {_BACKUP_RETENTION} most recent"
    )

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        backup_dir = db_path.parent / "backups"
        if not backup_dir.is_dir():
            return ValidationResult(ok=True, message="No backup directory yet")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        backup_dir = db_path.parent / "backups"
        if not backup_dir.is_dir():
            return ExecutionResult(
                success=True,
                message="No backups to clean up",
                data={"deleted": 0},
            )

        try:
            if progress_callback:
                progress_callback(0.2, "Scanning backups...")

            # Sort by modification time, newest first
            backups = sorted(
                [f for f in backup_dir.iterdir() if f.suffix == ".db"],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )

            to_delete = backups[_BACKUP_RETENTION:]
            if not to_delete:
                if progress_callback:
                    progress_callback(1.0, "Complete")
                return ExecutionResult(
                    success=True,
                    message=f"Only {len(backups)} backups — nothing to remove",
                    data={"deleted": 0, "kept": len(backups)},
                )

            total_bytes = 0
            for backup in to_delete:
                total_bytes += backup.stat().st_size
                backup.unlink()

            mb = total_bytes / (1024 * 1024)
            if progress_callback:
                progress_callback(1.0, "Complete")

            return ExecutionResult(
                success=True,
                message=(
                    f"Deleted {len(to_delete)} old backups ({mb:.1f} MB), "
                    f"kept {_BACKUP_RETENTION} most recent"
                ),
                data={
                    "deleted": len(to_delete),
                    "kept": _BACKUP_RETENTION,
                    "freed_mb": round(mb, 1),
                },
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 5


@registry.register
class OrphanedSupplementsTask(MaintenanceTask):
    name = "cleanup_orphaned_supplements"
    display_name = "Orphaned Supplement Cleanup"
    description = "Remove supplement DB entries whose files no longer exist on disk"

    def validate(self, params: dict) -> ValidationResult:
        db_path = _resolve_db_path(params)
        if not db_path or not db_path.exists():
            return ValidationResult(ok=False, message="Database not found")
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        db_path = _resolve_db_path(params)
        if not db_path:
            return ExecutionResult(success=False, message="Database path not available")

        try:
            if progress_callback:
                progress_callback(0.2, "Scanning supplements...")

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='supplements'"
            )
            if not cursor.fetchone():
                conn.close()
                return ExecutionResult(
                    success=True,
                    message="No supplements table",
                    data={"removed": 0},
                )

            cursor.execute("SELECT id, file_path FROM supplements")
            rows = cursor.fetchall()

            orphan_ids = []
            for row in rows:
                if not Path(row["file_path"]).is_file():
                    orphan_ids.append(row["id"])

            if progress_callback:
                progress_callback(0.6, f"Found {len(orphan_ids)} orphans...")

            if orphan_ids:
                placeholders = ",".join("?" * len(orphan_ids))
                cursor.execute(
                    f"DELETE FROM supplements WHERE id IN ({placeholders})",  # nosec B608
                    orphan_ids,
                )
                conn.commit()

            conn.close()
            if progress_callback:
                progress_callback(1.0, "Complete")

            return ExecutionResult(
                success=True,
                message=(
                    f"Removed {len(orphan_ids)} orphaned supplement entries"
                    if orphan_ids
                    else "No orphaned supplements found"
                ),
                data={"removed": len(orphan_ids), "total_checked": len(rows)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 10


@registry.register
class StagingCleanupTask(MaintenanceTask):
    name = "staging_cleanup"
    display_name = "Staging Directory Cleanup"
    description = "Remove leftover files from the conversion staging directory"

    def validate(self, params: dict) -> ValidationResult:
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        import os
        import sys

        # Resolve staging directory from config
        _backend = str(Path(__file__).parent.parent.parent)
        if _backend not in sys.path:
            sys.path.insert(0, _backend)
        try:
            _lib = str(Path(__file__).parent.parent.parent.parent)
            if _lib not in sys.path:
                sys.path.insert(0, _lib)
            from config import AUDIOBOOKS_STAGING
        except ImportError:
            staging = Path(
                os.environ.get("AUDIOBOOKS_STAGING", "/tmp/audiobook-staging")
            )
        else:
            staging = AUDIOBOOKS_STAGING

        if not staging.is_dir():
            return ExecutionResult(
                success=True,
                message="Staging directory does not exist",
                data={"deleted": 0},
            )

        try:
            if progress_callback:
                progress_callback(0.2, "Scanning staging directory...")

            # Check if any ffmpeg conversions are active — don't delete mid-conversion
            import subprocess

            result = subprocess.run(
                ["pgrep", "-f", "ffmpeg.*opus"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    message="Conversion in progress — skipping staging cleanup",
                    data={"deleted": 0, "reason": "active_conversion"},
                )

            deleted = 0
            total_bytes = 0
            for f in staging.rglob("*"):
                if f.is_file():
                    total_bytes += f.stat().st_size
                    f.unlink()
                    deleted += 1

            # Remove empty subdirectories
            for d in sorted(staging.rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()

            mb = total_bytes / (1024 * 1024)
            if progress_callback:
                progress_callback(1.0, "Complete")

            return ExecutionResult(
                success=True,
                message=(
                    f"Cleaned {deleted} files ({mb:.1f} MB) from staging"
                    if deleted
                    else "Staging directory already clean"
                ),
                data={"deleted": deleted, "freed_mb": round(mb, 1)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 10
