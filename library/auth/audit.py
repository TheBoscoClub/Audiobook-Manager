"""Audit logging for user management actions."""

import json

from .models import AuditLog


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
            return self.get_by_id(cursor.lastrowid)

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
