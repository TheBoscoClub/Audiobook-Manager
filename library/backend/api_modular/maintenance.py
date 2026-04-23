"""
Maintenance scheduling API blueprint.

Provides CRUD endpoints for maintenance windows, manual announcements,
task registry listing, and execution history.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from .auth import admin_if_enabled, get_current_user, guest_allowed

logger = logging.getLogger(__name__)

maintenance_bp = Blueprint("maintenance", __name__)

_db_path = None


def init_maintenance_routes(database_path):
    """Initialize with database path."""
    global _db_path
    _db_path = database_path


def _get_db():
    """Get a database connection."""
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_username():
    """Get current username for audit trail."""
    try:
        user = get_current_user()
        return user.username if user else "system"
    except Exception as e:
        logger.debug("Failed to get current user: %s", e)
        return "system"


# ---------- Maintenance Windows ----------


@maintenance_bp.route("/api/admin/maintenance/windows", methods=["GET"])
@admin_if_enabled
def list_windows():
    """List all maintenance windows."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM maintenance_windows ORDER BY created_at DESC").fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


def _compute_next_run_at(schedule_type, scheduled_at, cron_expression):
    """Compute next_run_at from schedule parameters.

    Returns (next_run_at, error_response) where error_response is None on success.
    """
    if schedule_type == "once" and scheduled_at:
        return scheduled_at, None
    if schedule_type == "recurring" and cron_expression:
        try:
            from croniter import croniter

            cron = croniter(cron_expression, datetime.now(timezone.utc))
            return cron.get_next(datetime).isoformat() + "Z", None
        except (ValueError, KeyError):  # fmt: skip
            return None, (jsonify({"error": "Invalid cron expression"}), 400)
    return None, None


def _validate_task_type(task_type):
    """Validate task_type against the registry. Returns error response or None."""
    try:
        from .maintenance_tasks import registry

        if not registry.get(task_type):
            available = [t["name"] for t in registry.list_all()]
            return (
                jsonify({"error": f"Unknown task_type '{task_type}'", "available": available}),
                400,
            )
    except ImportError:
        pass  # Registry not yet available
    return None


@maintenance_bp.route("/api/admin/maintenance/windows", methods=["POST"])
@admin_if_enabled
def create_window():
    """Create a new maintenance window."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    name = data.get("name")
    task_type = data.get("task_type")
    schedule_type = data.get("schedule_type")
    if not all([name, task_type, schedule_type]):
        return jsonify({"error": "name, task_type, schedule_type required"}), 400

    if schedule_type not in ("once", "recurring"):
        return jsonify({"error": "schedule_type must be 'once' or 'recurring'"}), 400

    # Validate task type
    err = _validate_task_type(task_type)
    if err:
        return err

    cron_expression = data.get("cron_expression")
    scheduled_at = data.get("scheduled_at")

    # Compute next_run_at
    next_run_at, err = _compute_next_run_at(schedule_type, scheduled_at, cron_expression)
    if err:
        return err

    task_params = json.dumps(data.get("task_params", {}))
    conn = _get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO maintenance_windows
               (name, description, task_type, task_params, schedule_type,
                cron_expression, scheduled_at, next_run_at,
                duration_minutes, lead_time_hours)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                data.get("description", ""),
                task_type,
                task_params,
                schedule_type,
                cron_expression,
                scheduled_at,
                next_run_at,
                data.get("duration_minutes", 30),
                data.get("lead_time_hours", 48),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM maintenance_windows WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return jsonify(dict(row)), 201
    finally:
        conn.close()


_ALLOWED_UPDATE_FIELDS = frozenset(
    {
        "name",
        "description",
        "task_type",
        "task_params",
        "cron_expression",
        "scheduled_at",
        "duration_minutes",
        "lead_time_hours",
        "status",
    }
)

_SAFE_COLUMNS = _ALLOWED_UPDATE_FIELDS | {"next_run_at"}


def _extract_allowed_fields(data):
    """Filter request data to only allowed update fields."""
    updates = {k: v for k, v in data.items() if k in _ALLOWED_UPDATE_FIELDS}
    if "task_params" in updates and isinstance(updates["task_params"], dict):
        updates["task_params"] = json.dumps(updates["task_params"])
    return updates


def _recompute_schedule(updates, data, existing):
    """Recompute next_run_at if schedule fields changed."""
    schedule_changed = "cron_expression" in updates or "scheduled_at" in updates
    if not schedule_changed:
        return

    stype = data.get("schedule_type", existing["schedule_type"])
    next_run, _ = _compute_next_run_at(
        stype, updates.get("scheduled_at"), updates.get("cron_expression")
    )
    if next_run is not None:
        updates["next_run_at"] = next_run


def _build_window_updates(data, existing):
    """Build sanitized update dict from request data."""
    updates = _extract_allowed_fields(data)
    _recompute_schedule(updates, data, existing)
    return {k: v for k, v in updates.items() if k in _SAFE_COLUMNS}


@maintenance_bp.route("/api/admin/maintenance/windows/<int:wid>", methods=["PUT"])
@admin_if_enabled
def update_window(wid):
    """Update a maintenance window."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    conn = _get_db()
    try:
        existing = conn.execute("SELECT * FROM maintenance_windows WHERE id = ?", (wid,)).fetchone()
        if not existing:
            return jsonify({"error": "Window not found"}), 404

        sanitized = _build_window_updates(data, existing)
        if not sanitized:
            return jsonify({"error": "No valid fields to update"}), 400

        set_clause = ", ".join(f'"{k}" = ?' for k in sanitized)
        values = list(sanitized.values()) + [wid]
        conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            "UPDATE maintenance_windows SET "  # noqa: S608  # nosec B608
            + set_clause
            + " WHERE id = ?",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM maintenance_windows WHERE id = ?", (wid,)).fetchone()
        return jsonify(dict(row))
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/windows/<int:wid>", methods=["DELETE"])
@admin_if_enabled
def delete_window(wid):
    """Delete or soft-delete a maintenance window."""
    conn = _get_db()
    try:
        has_history = conn.execute(
            "SELECT COUNT(*) FROM maintenance_history WHERE window_id = ?", (wid,)
        ).fetchone()[0]

        if has_history:
            conn.execute("UPDATE maintenance_windows SET status = 'cancelled' WHERE id = ?", (wid,))
        else:
            conn.execute("DELETE FROM maintenance_windows WHERE id = ?", (wid,))
        conn.commit()
        return jsonify({"ok": True, "soft_deleted": bool(has_history)})
    finally:
        conn.close()


# ---------- Manual Messages ----------


@maintenance_bp.route("/api/admin/maintenance/messages", methods=["GET"])
@admin_if_enabled
def list_messages():
    """List all manual maintenance messages."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM maintenance_messages ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/messages", methods=["POST"])
@admin_if_enabled
def create_message():
    """Create a manual maintenance message and push immediately."""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "message field required"}), 400

    username = _get_username()
    conn = _get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO maintenance_messages (message, created_by) VALUES (?, ?)",
            (data["message"], username),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM maintenance_messages WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        result = dict(row)

        # Push immediately via WebSocket (in-process, no DB round-trip)
        try:
            from .websocket import connection_manager

            connection_manager.broadcast({"type": "maintenance_announce", "messages": [result]})
        except Exception as e:
            logger.warning("WebSocket broadcast failed: %s", e)

        return jsonify(result), 201
    finally:
        conn.close()


@maintenance_bp.route("/api/admin/maintenance/messages/<int:mid>", methods=["DELETE"])
@admin_if_enabled
def dismiss_message(mid):
    """Permanently dismiss a manual message."""
    username = _get_username()
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE maintenance_messages
               SET dismissed_at = datetime('now'), dismissed_by = ?
               WHERE id = ?""",
            (username, mid),
        )
        conn.commit()

        # Push dismiss notification
        try:
            from .websocket import connection_manager

            connection_manager.broadcast({"type": "maintenance_dismiss", "message_id": mid})
        except Exception as e:
            logger.warning("WebSocket broadcast failed: %s", e)

        return jsonify({"ok": True})
    finally:
        conn.close()


# ---------- Public Announcements ----------


@maintenance_bp.route("/api/maintenance/announcements", methods=["GET"])
@guest_allowed
def get_announcements():
    """Public endpoint: active announcements for all users (including pre-login).

    Returns manual messages + windows within lead time.
    @guest_allowed populates g.user if session exists but never returns 401.
    """
    conn = _get_db()
    try:
        # Active manual messages
        messages = conn.execute("""SELECT id, message, created_by, created_at
               FROM maintenance_messages
               WHERE dismissed_at IS NULL
               ORDER BY created_at DESC""").fetchall()

        # Upcoming windows within lead time
        windows = conn.execute("""SELECT id, name, description, task_type, next_run_at,
                      duration_minutes, lead_time_hours
               FROM maintenance_windows
               WHERE status = 'active'
                 AND next_run_at IS NOT NULL
                 AND datetime(next_run_at, '-' || lead_time_hours || ' hours')
                     <= datetime('now')
               ORDER BY next_run_at ASC""").fetchall()

        return jsonify(
            {"messages": [dict(r) for r in messages], "windows": [dict(r) for r in windows]}
        )
    finally:
        conn.close()


# ---------- Task Registry ----------


@maintenance_bp.route("/api/admin/maintenance/tasks", methods=["GET"])
@admin_if_enabled
def list_tasks():
    """List registered maintenance task types."""
    try:
        from .maintenance_tasks import registry

        return jsonify(registry.list_all())
    except ImportError:
        return jsonify([])


# ---------- Execution History ----------


@maintenance_bp.route("/api/admin/maintenance/history", methods=["GET"])
@admin_if_enabled
def get_history():
    """Execution history for all maintenance windows."""
    conn = _get_db()
    try:
        rows = conn.execute("""SELECT h.*, w.name as window_name, w.task_type
               FROM maintenance_history h
               JOIN maintenance_windows w ON h.window_id = w.id
               ORDER BY h.started_at DESC
               LIMIT 100""").fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()
