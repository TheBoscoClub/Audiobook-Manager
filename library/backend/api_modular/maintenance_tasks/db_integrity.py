"""Database integrity check task."""
import logging
import sqlite3

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult
from .db_vacuum import _resolve_db_path

logger = logging.getLogger(__name__)


@registry.register
class DatabaseIntegrityTask(MaintenanceTask):
    name = "db_integrity"
    display_name = "Database Integrity Check"
    description = "Run PRAGMA integrity_check on all databases"

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
                progress_callback(0.3, "Running integrity check...")
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            ok = result[0] == "ok"
            if progress_callback:
                progress_callback(1.0, "Complete")
            return ExecutionResult(
                success=ok,
                message=f"Integrity: {result[0]}",
                data={"result": result[0], "database": str(db_path)},
            )
        except Exception as e:
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 60
