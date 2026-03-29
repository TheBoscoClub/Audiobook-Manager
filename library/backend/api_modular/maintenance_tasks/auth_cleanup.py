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

        results = {}
        try:
            if progress_callback:
                progress_callback(0.1, "Cleaning stale sessions...")

            # 1. Stale sessions (non-persistent, inactive > 30 min)
            from models import (
                PendingRecoveryRepository,
                PendingRegistrationRepository,
                SessionRepository,
            )

            sessions = SessionRepository(db)
            stale = sessions.cleanup_stale(grace_minutes=30)
            results["stale_sessions"] = stale

            if progress_callback:
                progress_callback(0.3, "Cleaning expired registrations...")

            # 2. Expired pending registrations
            registrations = PendingRegistrationRepository(db)
            expired_regs = registrations.cleanup_expired()
            results["expired_registrations"] = expired_regs

            if progress_callback:
                progress_callback(0.5, "Cleaning expired recovery tokens...")

            # 3. Expired pending recoveries
            recoveries = PendingRecoveryRepository(db)
            expired_rec = recoveries.cleanup_expired()
            results["expired_recoveries"] = expired_rec

            if progress_callback:
                progress_callback(0.7, "Cleaning old access requests...")

            # 4. Old completed/denied access requests
            from datetime import datetime, timedelta

            cutoff = datetime.now() - timedelta(days=_ACCESS_REQUEST_RETENTION_DAYS)
            cutoff_str = cutoff.isoformat()
            with db.connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM access_requests "
                    "WHERE status IN ('approved', 'denied') "
                    "AND requested_at < ?",
                    (cutoff_str,),
                )
                results["old_access_requests"] = cursor.rowcount

            if progress_callback:
                progress_callback(1.0, "Complete")

            total = sum(results.values())
            parts = []
            if results["stale_sessions"]:
                parts.append(f"{results['stale_sessions']} stale sessions")
            if results["expired_registrations"]:
                parts.append(
                    f"{results['expired_registrations']} expired registrations"
                )
            if results["expired_recoveries"]:
                parts.append(f"{results['expired_recoveries']} expired recoveries")
            if results["old_access_requests"]:
                parts.append(f"{results['old_access_requests']} old access requests")

            message = (
                f"Cleaned {total} records: {', '.join(parts)}"
                if parts
                else "No stale auth data found"
            )

            db.close()
            return ExecutionResult(success=True, message=message, data=results)

        except Exception as e:
            db.close()
            return ExecutionResult(success=False, message=str(e))

    def estimate_duration(self):
        return 5
