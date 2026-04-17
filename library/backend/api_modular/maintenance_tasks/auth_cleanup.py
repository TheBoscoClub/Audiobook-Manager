"""Auth database cleanup — expired tokens, stale sessions, old access requests."""

import logging
import sys
from pathlib import Path

from . import registry
from .base import ExecutionResult, MaintenanceTask, ValidationResult

logger = logging.getLogger(__name__)

# Days after which completed/denied access requests are purged
_ACCESS_REQUEST_RETENTION_DAYS = 90


def _get_auth_db():
    """Get an AuthDatabase instance from the auth module."""
    _auth_dir = str(Path(__file__).parent.parent.parent.parent / "auth")
    if _auth_dir not in sys.path:
        sys.path.insert(0, _auth_dir)
    from database import AuthDatabase

    return AuthDatabase()


def _cleanup_stale_sessions(db, results, progress_callback):
    """Clean up non-persistent sessions inactive > 30 min."""
    if progress_callback:
        progress_callback(0.1, "Cleaning stale sessions...")

    from models import SessionRepository

    sessions = SessionRepository(db)
    results["stale_sessions"] = sessions.cleanup_stale(grace_minutes=30)


def _cleanup_expired_registrations(db, results, progress_callback):
    """Clean up expired pending registrations."""
    if progress_callback:
        progress_callback(0.3, "Cleaning expired registrations...")

    from models import PendingRegistrationRepository

    registrations = PendingRegistrationRepository(db)
    results["expired_registrations"] = registrations.cleanup_expired()


def _cleanup_expired_recoveries(db, results, progress_callback):
    """Clean up expired pending recoveries."""
    if progress_callback:
        progress_callback(0.5, "Cleaning expired recovery tokens...")

    from models import PendingRecoveryRepository

    recoveries = PendingRecoveryRepository(db)
    results["expired_recoveries"] = recoveries.cleanup_expired()


def _cleanup_old_access_requests(db, results, progress_callback):
    """Clean up old completed/denied access requests."""
    if progress_callback:
        progress_callback(0.7, "Cleaning old access requests...")

    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=_ACCESS_REQUEST_RETENTION_DAYS)
    with db.connection() as conn:
        cursor = conn.execute(
            "DELETE FROM access_requests "
            "WHERE status IN ('approved', 'denied') "
            "AND requested_at < ?",
            (cutoff.isoformat(),),
        )
        results["old_access_requests"] = cursor.rowcount


def _build_summary(results):
    """Build human-readable summary from cleanup results."""
    labels = {
        "stale_sessions": "stale sessions",
        "expired_registrations": "expired registrations",
        "expired_recoveries": "expired recoveries",
        "old_access_requests": "old access requests",
    }
    parts = [f"{results[k]} {label}" for k, label in labels.items() if results.get(k)]
    total = sum(results.values())
    if parts:
        return f"Cleaned {total} records: {', '.join(parts)}"
    return "No stale auth data found"


@registry.register
class AuthCleanupTask(MaintenanceTask):
    name = "auth_cleanup"
    display_name = "Auth Data Cleanup"
    description = "Remove stale sessions, expired tokens, and old access requests"

    def validate(self, params: dict) -> ValidationResult:
        try:
            db = _get_auth_db()
            db.close()
            return ValidationResult(ok=True)
        except Exception as e:
            return ValidationResult(ok=False, message=f"Auth DB unavailable: {e}")

    def execute(self, params: dict, progress_callback=None) -> ExecutionResult:
        try:
            db = _get_auth_db()
        except Exception as e:
            return ExecutionResult(success=False, message=f"Cannot open auth DB: {e}")

        results: dict[str, int] = {}
        try:
            _cleanup_stale_sessions(db, results, progress_callback)
            _cleanup_expired_registrations(db, results, progress_callback)
            _cleanup_expired_recoveries(db, results, progress_callback)
            _cleanup_old_access_requests(db, results, progress_callback)

            if progress_callback:
                progress_callback(1.0, "Complete")

            db.close()
            return ExecutionResult(success=True, message=_build_summary(results), data=results)

        except Exception as e:
            db.close()
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 5
