"""Audit logging for user management actions."""

import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .models import AuditLog

logger = logging.getLogger(__name__)

# Actions that trigger admin notifications
CRITICAL_ACTIONS = {"change_username", "switch_auth_method", "reset_credentials", "delete_account"}


class AuditLogRepository:
    """Repository for audit log CRUD operations."""

    def __init__(self, db):
        self.db = db

    def log(self, actor_id, target_id, action, details=None):
        """Create an audit log entry. Returns the created entry."""
        details_json = json.dumps(details) if details else None
        with self.db.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO audit_log (actor_id, target_id, action, details) VALUES (?, ?, ?, ?)",
                (actor_id, target_id, action, details_json),
            )
            conn.commit()
            entry = self.get_by_id(cursor.lastrowid)

        # Push real-time notification to connected admin clients (best-effort)
        try:
            from backend.api_modular.websocket import connection_manager

            connection_manager.broadcast({"type": "audit_notify", "action": action})
        except Exception as e:
            logger.debug("audit websocket broadcast failed (non-fatal): %s", e)

        return entry

    def get_by_id(self, entry_id):
        """Get a single audit log entry by ID."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return AuditLog.from_row(row)

    def list(self, limit=50, offset=0, action_filter=None, user_filter=None):
        """List audit log entries, newest first."""
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if action_filter:
            query += " AND action = ?"
            params.append(action_filter)
        if user_filter is not None:
            query += " AND (actor_id = ? OR target_id = ?)"
            params.extend([user_filter, user_filter])
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.db.connection() as conn:
            cursor = conn.execute(query, params)
            return [AuditLog.from_row(row) for row in cursor.fetchall()]

    def count(self, action_filter=None, user_filter=None):
        """Count total audit log entries (for pagination)."""
        query = "SELECT COUNT(*) FROM audit_log WHERE 1=1"
        params = []
        if action_filter:
            query += " AND action = ?"
            params.append(action_filter)
        if user_filter is not None:
            query += " AND (actor_id = ? OR target_id = ?)"
            params.extend([user_filter, user_filter])
        with self.db.connection() as conn:
            return conn.execute(query, params).fetchone()[0]

    def count_unseen(self, last_seen_id):
        """Count entries newer than the given ID (for badge count)."""
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE id > ?", (last_seen_id,)
            ).fetchone()[0]


def notify_admins(action: str, details: dict, db) -> None:
    """Send notifications to all admins for critical actions.

    In-app: handled by badge count (count_unseen).
    Email: sent to all admins with a recovery_email set.
    """
    if action not in CRITICAL_ACTIONS:
        return

    from .models import UserRepository

    user_repo = UserRepository(db)
    admins = [u for u in user_repo.list_all() if u.is_admin and u.recovery_email]

    if not admins:
        return

    subject, body = _format_notification(action, details)
    for admin in admins:
        if admin.recovery_email is not None:
            _send_notification_email(admin.recovery_email, subject, body)


def _format_notification(action: str, details: dict) -> tuple:
    """Format email subject and body for an audit action."""
    actor = details.get("actor_username", "Unknown")
    target = details.get("target_username", actor)
    action_labels = {
        "change_username": f'{target} changed username to "{details.get("new", "?")}"',
        "switch_auth_method": f"{target} switched auth method to {details.get('new', '?')}",
        "reset_credentials": f"{target} reset their credentials",
        "delete_account": f"{details.get('username', target)} deleted their account",
    }
    description = action_labels.get(action, f"{action} on {target}")
    subject = f"[Audiobook Library] Account change: {description}"
    body = (
        f"{description} at {details.get('timestamp', 'unknown time')}.\n\n"
        f"Actor: {actor}\n"
        f"Review in Back Office \u2192 Users \u2192 Audit Log."
    )
    return subject, body


def _send_notification_email(to_email: str, subject: str, body: str) -> bool:
    """Send a notification email via configured SMTP."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "noreply@localhost")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error("Failed to send audit notification to %s: %s", to_email, e)
        return False
