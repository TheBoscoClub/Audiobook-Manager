"""Database backup task."""
import logging
import sqlite3
from datetime import datetime, timezone

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)


@registry.register
class DatabaseBackupTask(MaintenanceTask):
    name = "db_backup"
    display_name = "Database Backup"
    description = "Create a timestamped backup of the library database"

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
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"{db_path.stem}-{timestamp}.db"

            if progress_callback:
                progress_callback(0.2, "Creating backup...")

            # Use SQLite online backup API for consistency
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(backup_path))
            src.backup(dst)
            src.close()
            dst.close()

            size_mb = backup_path.stat().st_size / (1024 * 1024)
            if progress_callback:
                progress_callback(1.0, "Complete")

            return ExecutionResult(
                success=True,
                message=f"Backup created: {backup_path.name} ({size_mb:.1f} MB)",
                data={"backup_path": str(backup_path), "size_mb": round(size_mb, 1)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 30
