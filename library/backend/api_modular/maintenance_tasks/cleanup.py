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


def _report_progress(callback, fraction, message):
    """Call progress callback if provided."""
    if callback:
        callback(fraction, message)


def _delete_files(file_list):
    """Delete a list of Path objects and return total bytes freed."""
    total_bytes = 0
    for f in file_list:
        total_bytes += f.stat().st_size
        f.unlink()
    return total_bytes


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
            _report_progress(progress_callback, 0.2, "Scanning backups...")

            backups = sorted(
                [f for f in backup_dir.iterdir() if f.suffix == ".db"],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )

            to_delete = backups[_BACKUP_RETENTION:]
            if not to_delete:
                _report_progress(progress_callback, 1.0, "Complete")
                return ExecutionResult(
                    success=True,
                    message=f"Only {len(backups)} backups — nothing to remove",
                    data={"deleted": 0, "kept": len(backups)},
                )

            total_bytes = _delete_files(to_delete)
            mb = total_bytes / (1024 * 1024)
            _report_progress(progress_callback, 1.0, "Complete")

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


def _find_orphan_supplement_ids(cursor):
    """Find supplement IDs whose files no longer exist on disk.

    Returns (orphan_ids, total_checked).
    """
    cursor.execute("SELECT id, file_path FROM supplements")
    rows = cursor.fetchall()
    orphan_ids = [row["id"] for row in rows if not Path(row["file_path"]).is_file()]
    return orphan_ids, len(rows)


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
            _report_progress(progress_callback, 0.2, "Scanning supplements...")

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

            orphan_ids, total_checked = _find_orphan_supplement_ids(cursor)
            _report_progress(
                progress_callback, 0.6, f"Found {len(orphan_ids)} orphans..."
            )

            if orphan_ids:
                placeholders = ",".join("?" * len(orphan_ids))
                cursor.execute(
                    f"DELETE FROM supplements WHERE id IN ({placeholders})",  # nosec B608
                    orphan_ids,
                )
                conn.commit()

            conn.close()
            _report_progress(progress_callback, 1.0, "Complete")

            return ExecutionResult(
                success=True,
                message=(
                    f"Removed {len(orphan_ids)} orphaned supplement entries"
                    if orphan_ids
                    else "No orphaned supplements found"
                ),
                data={"removed": len(orphan_ids), "total_checked": total_checked},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 10


def _resolve_staging_dir():
    """Resolve the staging directory path from config or environment."""
    import os
    import sys

    _backend = str(Path(__file__).parent.parent.parent)
    if _backend not in sys.path:
        sys.path.insert(0, _backend)
    try:
        _lib = str(Path(__file__).parent.parent.parent.parent)
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        from config import AUDIOBOOKS_STAGING

        return AUDIOBOOKS_STAGING
    except ImportError:
        return Path(os.environ.get("AUDIOBOOKS_STAGING", "/tmp/audiobook-staging"))  # nosec B108 — config fallback


def _is_conversion_active():
    """Check if any ffmpeg conversions are currently running."""
    import subprocess

    result = subprocess.run(
        ["pgrep", "-f", "ffmpeg.*opus"],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


def _clean_staging_files(staging):
    """Delete all files in staging directory and remove empty subdirs.

    Returns (deleted_count, freed_bytes).
    """
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

    return deleted, total_bytes


@registry.register
class StagingCleanupTask(MaintenanceTask):
    name = "staging_cleanup"
    display_name = "Staging Directory Cleanup"
    description = "Remove leftover files from the conversion staging directory"

    def validate(self, params: dict) -> ValidationResult:
        return ValidationResult(ok=True)

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        staging = _resolve_staging_dir()

        if not staging.is_dir():
            return ExecutionResult(
                success=True,
                message="Staging directory does not exist",
                data={"deleted": 0},
            )

        try:
            _report_progress(progress_callback, 0.2, "Scanning staging directory...")

            if _is_conversion_active():
                return ExecutionResult(
                    success=True,
                    message="Conversion in progress — skipping staging cleanup",
                    data={"deleted": 0, "reason": "active_conversion"},
                )

            deleted, total_bytes = _clean_staging_files(staging)
            mb = total_bytes / (1024 * 1024)
            _report_progress(progress_callback, 1.0, "Complete")

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
