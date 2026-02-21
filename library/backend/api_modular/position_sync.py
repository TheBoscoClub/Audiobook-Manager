"""
Position API Module

Provides per-user playback position tracking for the audiobook library.

When auth is enabled, positions are stored per-user in the encrypted auth database.
When auth is disabled, positions are stored globally in the library database.

Endpoints:
    GET  /api/position/<id>    - Get position for a single book
    PUT  /api/position/<id>    - Update local position
    GET  /api/position/status  - Get position tracking status
"""

from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from .auth import auth_if_enabled, get_auth_db, get_current_user

# Import auth models for per-user position tracking
try:
    from auth import (
        ListeningHistoryRepository,
        PositionRepository,
        UserListeningHistory,
        UserPosition,
    )

    POSITION_REPO_AVAILABLE = True
except ImportError:
    POSITION_REPO_AVAILABLE = False

# Blueprint for position routes
position_bp = Blueprint("position", __name__, url_prefix="/api/position")

# Module-level database path (set by init function)
_db_path = None


def init_position_routes(database_path: Path):
    """Initialize position routes with database path."""
    global _db_path
    _db_path = database_path


def ms_to_human(ms: int) -> str:
    """Convert milliseconds to human-readable format."""
    if ms is None or ms == 0:
        return "0s"
    seconds = ms // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def get_db():
    """Get database connection using module's database path."""
    import sqlite3

    if _db_path is None:
        raise RuntimeError(
            "Position routes not initialized. Call init_position_routes first."
        )
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _is_auth_enabled() -> bool:
    """Check if auth is enabled in the current app."""
    return current_app.config.get("AUTH_ENABLED", False)


def _get_user_position(user_id: int, audiobook_id: int) -> int:
    """
    Get user's position from the encrypted auth database.

    Returns position in milliseconds, 0 if not found.
    """
    if not POSITION_REPO_AVAILABLE:
        return 0

    try:
        auth_db = get_auth_db()
        repo = PositionRepository(auth_db)
        pos = repo.get(user_id, audiobook_id)
        return pos.position_ms if pos else 0
    except RuntimeError:
        return 0


def _save_user_position(user_id: int, audiobook_id: int, position_ms: int) -> bool:
    """
    Save user's position to the encrypted auth database.

    Returns True on success, False on failure.
    """
    if not POSITION_REPO_AVAILABLE:
        return False

    try:
        auth_db = get_auth_db()
        pos = UserPosition(
            user_id=user_id, audiobook_id=audiobook_id, position_ms=position_ms
        )
        pos.save(auth_db)
        return True
    except RuntimeError:
        return False


def _update_listening_history(
    user_id: int, audiobook_id: int, position_ms: int
) -> None:
    """
    Create or update a listening history entry for the user and audiobook.

    If an open session exists (ended_at IS NULL), update it with the new position.
    Otherwise, create a new session starting at the current position.
    """
    if not POSITION_REPO_AVAILABLE:
        return

    try:
        auth_db = get_auth_db()
        repo = ListeningHistoryRepository(auth_db)
        audiobook_id_str = str(audiobook_id)

        session = repo.get_open_session(user_id, audiobook_id_str)
        now = datetime.now()

        if session:
            # Update existing open session
            session.ended_at = now
            session.position_end_ms = position_ms
            if session.position_start_ms is not None:
                session.duration_listened_ms = max(
                    0, position_ms - session.position_start_ms
                )
            session.save(auth_db)
        else:
            # Create new listening session
            entry = UserListeningHistory(
                user_id=user_id,
                audiobook_id=audiobook_id_str,
                started_at=now,
                position_start_ms=position_ms,
            )
            entry.save(auth_db)
    except RuntimeError:
        # Auth DB not available — skip silently
        pass


# ============================================================
# API Endpoints
# ============================================================


@position_bp.route("/status", methods=["GET"])
@auth_if_enabled
def position_status():
    """Check position tracking status."""
    return jsonify(
        {
            "per_user": _is_auth_enabled(),
        }
    )


@position_bp.route("/<int:audiobook_id>", methods=["GET"])
@auth_if_enabled
def get_position(audiobook_id: int):
    """
    Get playback position for a single audiobook.

    When auth is enabled, returns the current user's personal position.
    When auth is disabled, returns the global position.
    """
    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, title, asin, duration_hours,
                   playback_position_ms, playback_position_updated
            FROM audiobooks WHERE id = ?
        """,
            (audiobook_id,),
        )

        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Audiobook not found"}), 404

        duration_ms = int((row["duration_hours"] or 0) * 3600000)

        # Get position: per-user when auth enabled, global otherwise
        if _is_auth_enabled():
            user = get_current_user()
            local_pos = _get_user_position(user.id, audiobook_id) if user else 0
        else:
            local_pos = row["playback_position_ms"] or 0

        percent = round(local_pos / duration_ms * 100, 1) if duration_ms > 0 else 0

        return jsonify(
            {
                "id": row["id"],
                "title": row["title"],
                "asin": row["asin"],
                "duration_ms": duration_ms,
                "duration_human": ms_to_human(duration_ms),
                "local_position_ms": local_pos,
                "local_position_human": ms_to_human(local_pos),
                "local_position_updated": row["playback_position_updated"],
                "percent_complete": percent,
            }
        )
    finally:
        conn.close()


@position_bp.route("/<int:audiobook_id>", methods=["PUT"])
@auth_if_enabled
def update_position(audiobook_id: int):
    """
    Update playback position for an audiobook.

    When auth is enabled, saves to the current user's personal position (encrypted).
    When auth is disabled, saves to the global position in the library database.
    """
    data = request.get_json()
    position_ms = data.get("position_ms")

    if position_ms is None:
        return jsonify({"error": "position_ms required"}), 400

    conn = get_db()
    try:
        cursor = conn.cursor()

        # Verify audiobook exists
        cursor.execute("SELECT id FROM audiobooks WHERE id = ?", (audiobook_id,))
        if not cursor.fetchone():
            return jsonify({"error": "Audiobook not found"}), 404

        now = datetime.now().isoformat()

        # Save position: per-user when auth enabled, global otherwise
        if _is_auth_enabled():
            user = get_current_user()
            if user:
                if not _save_user_position(user.id, audiobook_id, position_ms):
                    return jsonify({"error": "Failed to save position"}), 500
                # Create/update listening history entry
                _update_listening_history(user.id, audiobook_id, position_ms)
            else:
                return jsonify({"error": "User not found"}), 401
        else:
            # Single-user mode: update global position
            cursor.execute(
                """
                UPDATE audiobooks
                SET playback_position_ms = ?,
                    playback_position_updated = ?,
                    updated_at = ?
                WHERE id = ?
            """,
                (position_ms, now, now, audiobook_id),
            )

            # Record in global history
            cursor.execute(
                """
                INSERT INTO playback_history (audiobook_id, position_ms, source)
                VALUES (?, ?, 'local')
            """,
                (audiobook_id, position_ms),
            )

            conn.commit()

        return jsonify(
            {
                "success": True,
                "audiobook_id": audiobook_id,
                "position_ms": position_ms,
                "position_human": ms_to_human(position_ms),
                "updated_at": now,
            }
        )
    finally:
        conn.close()
