"""
The local maintenance door: /api/local/maintenance/*

For a root script on the same host (the audiobook-maintenance command): who
is connected, post and clear an announcement, schedule a host-downtime
window. Design: docs/superpowers/specs/2026-11-12-maintenance-cli-design.md.

Three locks; a request must pass all of them:

1. The peer is a loopback address AND the request carries no forwarding
   header. The reverse proxy re-authors X-Real-IP / X-Forwarded-For on
   everything it relays, so anything that came through the front door is
   refused here even if the proxy's own refusal of /api/local/ were bypassed.
2. A bearer token equal to the one this process wrote into its run directory
   at start (mode 0400, service user). No token issued → nobody gets in.
3. (In proxy_server.py) the proxy does not forward /api/local/ at all.

The session cookie is not consulted on these paths, and the token opens
nothing else.
"""

import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from .auth_shared import is_loopback_address

logger = logging.getLogger(__name__)

maintenance_local_bp = Blueprint("maintenance_local", __name__, url_prefix="/api/local/maintenance")

CREATED_BY = "maintenance-cli"
HOST_DOWNTIME = "host_downtime"
_FORWARDING_HEADERS = ("X-Real-IP", "X-Forwarded-For")

_db_path = None

# The connection list lives in this process's memory and starts empty. The
# status answer carries the process's age so the caller can tell "nobody is
# here" from "nobody has reconnected yet".
_STARTED = time.monotonic()


def init_maintenance_local(flask_app, database_path):
    """Bind the database and start with the door shut (no token issued)."""
    global _db_path
    _db_path = database_path
    flask_app.config.setdefault("MAINTENANCE_TOKEN", None)


def issue_maintenance_token(flask_app, token_path) -> bool:
    """Write a fresh token to ``token_path`` (0400) and arm the door with it.

    Called once at server start, never by create_app(): tests and tools that
    build an app must not write into the installation's run directory.
    Returns False, leaving the door shut, when the file cannot be written.
    """
    token_path = Path(token_path)
    token = secrets.token_hex(32)
    tmp_path = token_path.with_name(f".{token_path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            os.write(fd, token.encode("ascii"))
        finally:
            os.close(fd)
        os.replace(tmp_path, token_path)
    except OSError as e:
        # nosemgrep: python-logger-credential-disclosure  # Reason: logs the OSError (path + errno) from a failed write; the token value is never formatted into any log line
        logger.warning("Maintenance token not issued (%s); the local door stays shut", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        flask_app.config["MAINTENANCE_TOKEN"] = None
        return False
    flask_app.config["MAINTENANCE_TOKEN"] = token
    return True


def _from_this_host_directly() -> bool:
    if not is_loopback_address(request.remote_addr or ""):
        return False
    return not any(request.headers.get(h) for h in _FORWARDING_HEADERS)


def local_token_required(f):
    """Apply locks 1 and 2 (module docstring)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not _from_this_host_directly():
            return jsonify({"error": "Not found"}), 404  # hide the door
        expected = current_app.config.get("MAINTENANCE_TOKEN")
        if not expected:
            return jsonify({"error": "Maintenance token not issued"}), 503
        scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(
            presented.strip().encode("utf-8"), expected.encode("utf-8")
        ):
            return jsonify({"error": "Invalid maintenance token"}), 401
        return f(*args, **kwargs)

    return decorated


def _get_db():
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _broadcast(message):
    try:
        from .websocket import connection_manager

        connection_manager.broadcast(message)
    except Exception as e:  # the row is stored; page loads still show it
        logger.warning("WebSocket broadcast failed: %s", e)


# ------------------------------------------------------------------ status


@maintenance_local_bp.route("/status", methods=["GET"])
@local_token_required
def status():
    """Who is connected: WebSocket clients plus recent announcement pollers."""
    from .websocket import connection_manager, poll_tracker

    users = list(connection_manager.admin_connections_list()["users"])
    users.extend(poll_tracker.recent(exclude=connection_manager.session_ids()))
    return jsonify(
        {
            "uptime_seconds": int(time.monotonic() - _STARTED),
            "connected": len(users),
            "listening": sum(1 for u in users if u["state"] == "listening"),
            "polling": sum(1 for u in users if u["state"] == poll_tracker.UNSEEN),
            "users": users,
        }
    )


# ----------------------------------------------------------- announcements


@maintenance_local_bp.route("/announce", methods=["POST"])
@local_token_required
def announce():
    """Create an announcement and push it to every connected browser."""
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message required"}), 400

    conn = _get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO maintenance_messages (message, created_by) VALUES (?, ?)",
            (message.strip(), CREATED_BY),
        )
        conn.commit()
        row = dict(
            conn.execute(
                "SELECT * FROM maintenance_messages WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        )
    finally:
        conn.close()

    _broadcast({"type": "maintenance_announce", "messages": [row]})
    return jsonify({"id": row["id"]}), 201


@maintenance_local_bp.route("/clear", methods=["POST"])
@local_token_required
def clear():
    """Dismiss announcements this door created: one by id, or all of them."""
    data = request.get_json(silent=True) or {}
    wanted = data.get("id")
    if wanted is not None and (isinstance(wanted, bool) or not isinstance(wanted, int)):
        return jsonify({"error": "id must be an integer"}), 400

    sql = "SELECT id FROM maintenance_messages WHERE dismissed_at IS NULL AND created_by = ?"
    args: list = [CREATED_BY]
    if wanted is not None:
        sql += " AND id = ?"
        args.append(wanted)

    conn = _get_db()
    try:
        ids = [r["id"] for r in conn.execute(sql, args).fetchall()]
        if wanted is not None and not ids:
            return jsonify({"error": "No open announcement of this command with that id"}), 404
        for mid in ids:
            conn.execute(
                "UPDATE maintenance_messages SET dismissed_at = datetime('now'), dismissed_by = ? "
                "WHERE id = ?",
                (CREATED_BY, mid),
            )
        conn.commit()
    finally:
        conn.close()

    for mid in ids:
        _broadcast({"type": "maintenance_dismiss", "message_id": mid})
    return jsonify({"cleared": len(ids), "ids": ids})


# ------------------------------------------------------ host-downtime window


def _parse_start(value):
    """An ISO time that names its zone → the UTC text the browser UI stores."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # a time without a zone could mean two different moments
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _whole_number(value, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


@maintenance_local_bp.route("/window", methods=["POST"])
@local_token_required
def add_window():
    """Schedule a host-downtime window: announced ahead of time, runs no task."""
    data = request.get_json(silent=True) or {}
    start = _parse_start(data.get("start"))
    minutes = _whole_number(data.get("minutes"), 1)
    lead_hours = _whole_number(data.get("lead_hours"), 0)
    raw_message = data.get("message")
    message = raw_message.strip() if isinstance(raw_message, str) else ""
    name = data.get("name") or "Host maintenance"

    problems = []
    if start is None:
        problems.append("start must be an ISO time with a zone, e.g. 2031-01-25T03:00:00-06:00")
    if minutes is None:
        problems.append("minutes must be a whole number of at least 1")
    if lead_hours is None:
        problems.append("lead_hours must be a whole number of at least 0")
    if not message:
        problems.append("message required")
    if not isinstance(name, str):
        problems.append("name must be text")
    if problems:
        return jsonify({"error": "; ".join(problems)}), 400

    conn = _get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO maintenance_windows
               (name, description, task_type, task_params, schedule_type,
                scheduled_at, next_run_at, duration_minutes, lead_time_hours)
               VALUES (?, ?, ?, ?, 'once', ?, ?, ?, ?)""",
            (
                name.strip(),
                message,
                HOST_DOWNTIME,
                json.dumps({"created_by": CREATED_BY}),
                start,
                start,
                minutes,
                lead_hours,
            ),
        )
        conn.commit()
        return jsonify({"id": cursor.lastrowid, "start": start}), 201
    finally:
        conn.close()


def _created_here(row) -> bool:
    if row is None or row["task_type"] != HOST_DOWNTIME or row["status"] != "active":
        return False
    try:
        params = json.loads(row["task_params"] or "{}")
    except ValueError:
        return False
    return isinstance(params, dict) and params.get("created_by") == CREATED_BY


@maintenance_local_bp.route("/window/cancel", methods=["POST"])
@local_token_required
def cancel_window():
    """Cancel a host-downtime window this door created."""
    data = request.get_json(silent=True) or {}
    wid = _whole_number(data.get("id"), 1)
    if wid is None:
        return jsonify({"error": "id must be a positive integer"}), 400

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM maintenance_windows WHERE id = ?", (wid,)).fetchone()
        if not _created_here(row):
            return jsonify({"error": "No active window of this command with that id"}), 404
        conn.execute("UPDATE maintenance_windows SET status = 'cancelled' WHERE id = ?", (wid,))
        conn.commit()
        return jsonify({"cancelled": wid})
    finally:
        conn.close()
