"""Database vacuum and optimize task."""
import logging
import sqlite3
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult

logger = logging.getLogger(__name__)


def _resolve_db_path(params):
    """Resolve database path from params or Flask app context.

    Handlers run in two contexts:
    - Flask request (API validation): current_app.config available
    - Scheduler daemon (standalone): db_path passed via params
    """
    if "db_path" in params:
        return Path(params["db_path"])
    try:
        from flask import current_app
        return current_app.config["DATABASE_PATH"]
    except (RuntimeError, ImportError):
        return None


@registry.register
class DatabaseVacuumTask(MaintenanceTask):
    name = "db_vacuum"
    display_name = "Database Vacuum & Optimize"
    description = "Run VACUUM and ANALYZE on the library database"

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
            conn = sqlite3.connect(str(db_path))
            if progress_callback:
                progress_callback(0.2, "Running ANALYZE...")
            conn.execute("ANALYZE")
            if progress_callback:
                progress_callback(0.5, "Running VACUUM...")
            conn.execute("VACUUM")
            conn.close()
            if progress_callback:
                progress_callback(1.0, "Complete")
            return ExecutionResult(
                success=True,
                message="VACUUM and ANALYZE completed",
                data={"database": str(db_path)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 30
