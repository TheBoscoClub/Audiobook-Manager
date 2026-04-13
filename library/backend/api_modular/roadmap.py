"""
Roadmap API blueprint.

Public read access for all users, admin-only write access.
Displayed in the Help section of the web UI.
"""

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from .auth import admin_if_enabled

roadmap_bp = Blueprint("roadmap", __name__)

_db_path = None

VALID_STATUSES = ("planned", "in_progress", "completed", "cancelled")
VALID_PRIORITIES = ("low", "medium", "high")


def init_roadmap_routes(database_path):
    """Initialize with database path."""
    global _db_path
    _db_path = database_path


def _get_db():
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@roadmap_bp.route("/api/roadmap", methods=["GET"])
def get_roadmap():
    """Public: list all non-cancelled roadmap items."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM roadmap_items WHERE status != 'cancelled' "
        "ORDER BY sort_order ASC, created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@roadmap_bp.route("/api/admin/roadmap", methods=["GET"])
@admin_if_enabled
def admin_get_roadmap():
    """Admin: list all roadmap items including cancelled."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM roadmap_items ORDER BY sort_order ASC, created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@roadmap_bp.route("/api/admin/roadmap", methods=["POST"])
@admin_if_enabled
def create_roadmap_item():
    """Admin: create a roadmap item."""
    data = request.get_json()
    if not data or not data.get("title"):
        return jsonify({"error": "title is required"}), 400

    status = data.get("status", "planned")
    priority = data.get("priority", "medium")
    if status not in VALID_STATUSES:
        return jsonify({"error": f"Invalid status. Use: {VALID_STATUSES}"}), 400
    if priority not in VALID_PRIORITIES:
        return jsonify({"error": f"Invalid priority. Use: {VALID_PRIORITIES}"}), 400

    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    cursor = conn.execute(
        "INSERT INTO roadmap_items (title, description, status, priority, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            data["title"],
            data.get("description", ""),
            status,
            priority,
            data.get("sort_order", 0),
            now,
            now,
        ),
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"id": item_id, "message": "Created"}), 201


@roadmap_bp.route("/api/admin/roadmap/<int:item_id>", methods=["PUT"])
@admin_if_enabled
def update_roadmap_item(item_id):
    """Admin: update a roadmap item."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    conn = _get_db()
    existing = conn.execute(
        "SELECT id FROM roadmap_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    fields = []
    params = []
    for col in ("title", "description", "status", "priority", "sort_order"):
        if col in data:
            if col == "status" and data[col] not in VALID_STATUSES:
                conn.close()
                return jsonify({"error": f"Invalid status. Use: {VALID_STATUSES}"}), 400
            if col == "priority" and data[col] not in VALID_PRIORITIES:
                conn.close()
                return jsonify(
                    {"error": f"Invalid priority. Use: {VALID_PRIORITIES}"}
                ), 400
            fields.append(f"{col} = ?")
            params.append(data[col])

    if not fields:
        conn.close()
        return jsonify({"error": "No valid fields to update"}), 400

    fields.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(item_id)

    conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"UPDATE roadmap_items SET {', '.join(fields)} WHERE id = ?",  # nosec B608
        params,
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Updated"})


@roadmap_bp.route("/api/admin/roadmap/<int:item_id>", methods=["DELETE"])
@admin_if_enabled
def delete_roadmap_item(item_id):
    """Admin: delete a roadmap item."""
    conn = _get_db()
    result = conn.execute("DELETE FROM roadmap_items WHERE id = ?", (item_id,))
    conn.commit()
    deleted = result.rowcount
    conn.close()
    if deleted == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"message": "Deleted"})
