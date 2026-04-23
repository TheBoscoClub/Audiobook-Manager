"""
User State API Module

Provides per-user endpoints for listening history, download tracking,
personal library, and new book discovery.

All endpoints require authentication (@login_required).

Endpoints:
    GET  /api/user/history              - Paginated listening history
    GET  /api/user/downloads            - Paginated download history
    POST /api/user/downloads/<id>/complete - Record completed download
    GET  /api/user/library              - Distinct books user has interacted with
    GET  /api/user/library?hidden=true  - Only hidden books from My Library
    POST /api/user/library/hide         - Hide books from My Library view
    POST /api/user/library/unhide       - Unhide books (restore to My Library)
    GET  /api/user/new-books            - Books added after user's last seen timestamp
    POST /api/user/new-books/dismiss    - Update new_books_seen_at preference
"""

import sqlite3
from datetime import datetime

# Import auth models for per-user state
from auth import (
    DownloadRepository,
    HiddenBookRepository,
    ListeningHistoryRepository,
    PositionRepository,
    PreferencesRepository,
    UserDownload,
)
from flask import Blueprint, jsonify, request

from .auth import get_auth_db, login_required, require_current_user

# Blueprint for user state routes
user_bp = Blueprint("user_state", __name__, url_prefix="/api/user")

# Module-level database path (library DB — set by init function)
_db_path: str | None = None


def init_user_state_routes(database_path: str) -> None:
    """Initialize user state routes with the library database path."""
    global _db_path
    _db_path = database_path


def _get_library_db() -> sqlite3.Connection:
    """Get library database connection."""
    if _db_path is None:
        raise RuntimeError("User state routes not initialized. Call init_user_state_routes first.")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ============================================================
# GET /api/user/history — Paginated listening history
# ============================================================


@user_bp.route("/history", methods=["GET"])
@login_required
def get_history():
    """
    Get the current user's listening history (most recent first).

    Query params:
        limit:  Number of records (default 50, max 200)
        offset: Pagination offset (default 0)
    """
    user = require_current_user()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (ValueError, TypeError):  # fmt: skip
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):  # fmt: skip
        offset = 0

    auth_db = get_auth_db()
    repo = ListeningHistoryRepository(auth_db)
    items = repo.get_for_user(user.ensured_id, limit=limit, offset=offset)

    return jsonify(
        {
            "items": [
                {
                    "id": h.id,
                    "audiobook_id": h.audiobook_id,
                    "started_at": h.started_at.isoformat() if h.started_at else None,
                    "ended_at": h.ended_at.isoformat() if h.ended_at else None,
                    "position_start_ms": h.position_start_ms,
                    "position_end_ms": h.position_end_ms,
                    "duration_listened_ms": h.duration_listened_ms,
                }
                for h in items
            ],
            "count": len(items),
            "limit": limit,
            "offset": offset,
        }
    )


# ============================================================
# GET /api/user/downloads — Paginated download history
# ============================================================


@user_bp.route("/downloads", methods=["GET"])
@login_required
def get_downloads():
    """
    Get the current user's download history (most recent first).

    Query params:
        limit:  Number of records (default 50, max 200)
        offset: Pagination offset (default 0)
    """
    user = require_current_user()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (ValueError, TypeError):  # fmt: skip
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):  # fmt: skip
        offset = 0

    auth_db = get_auth_db()
    repo = DownloadRepository(auth_db)
    items = repo.get_for_user(user.ensured_id, limit=limit, offset=offset)

    return jsonify(
        {
            "items": [
                {
                    "id": d.id,
                    "audiobook_id": d.audiobook_id,
                    "downloaded_at": (d.downloaded_at.isoformat() if d.downloaded_at else None),
                    "file_format": d.file_format,
                }
                for d in items
            ],
            "count": len(items),
            "limit": limit,
            "offset": offset,
        }
    )


# ============================================================
# POST /api/user/downloads/<id>/complete — Record download
# ============================================================


@user_bp.route("/downloads/<int:audiobook_id>/complete", methods=["POST"])
@login_required
def record_download_complete(audiobook_id: int):
    """
    Record that the current user completed downloading an audiobook.

    JSON body (optional):
        file_format: Format of the downloaded file (e.g., "opus", "mp3")
    """
    user = require_current_user()
    if user.id is None:
        return jsonify({"error": "User not found"}), 401

    # Verify the audiobook exists and get title for denormalized storage
    conn = _get_library_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title FROM audiobooks WHERE id = ?", (audiobook_id,))
        book_row = cursor.fetchone()
        if not book_row:
            return jsonify({"error": "Audiobook not found"}), 404
        book_title = book_row[1]
    finally:
        conn.close()

    # Extract optional format from body
    data = request.get_json(silent=True) or {}
    file_format = data.get("file_format")

    auth_db = get_auth_db()
    download = UserDownload(
        user_id=user.id, audiobook_id=str(audiobook_id), title=book_title, file_format=file_format
    )
    download.save(auth_db)

    return jsonify({"success": True, "download_id": download.id, "audiobook_id": audiobook_id})


# ============================================================
# GET /api/user/library — Distinct books user has interacted with
# ============================================================


def _collect_user_book_ids(auth_db, user_id):
    """Collect all audiobook IDs from user's history, downloads, and positions.

    Returns (all_ids, history_ids, download_ids, position_ids).
    """
    history_repo = ListeningHistoryRepository(auth_db)
    download_repo = DownloadRepository(auth_db)
    position_repo = PositionRepository(auth_db)

    history_ids = set(history_repo.get_user_book_ids(user_id))
    download_ids = set(download_repo.get_user_book_ids(user_id))
    positions = position_repo.get_all_for_user(user_id)
    position_ids = {str(p.audiobook_id) for p in positions}

    return (history_ids | download_ids | position_ids, history_ids, download_ids, position_ids)


def _apply_hidden_filter(all_ids, hidden_ids, show_hidden):
    """Apply hidden books filter and return (filtered_ids, hidden_count)."""
    hidden_count = len(all_ids & hidden_ids)
    if show_hidden:
        return all_ids & hidden_ids, hidden_count
    return all_ids - hidden_ids, hidden_count


def _build_last_listened_map(history_repo, user_id, history_ids):
    """Build mapping of audiobook_id -> most recent listened ISO timestamp."""
    if not history_ids:
        return {}
    result: dict[str, str] = {}
    for h in history_repo.get_for_user(user_id, limit=10000, offset=0):
        ts = h.ended_at or h.started_at
        if ts is None:
            continue
        aid = str(h.audiobook_id)
        ts_iso = ts.isoformat()
        if aid not in result or ts_iso > result[aid]:
            result[aid] = ts_iso
    return result


def _build_downloaded_at_map(download_repo, user_id, download_ids):
    """Build mapping of audiobook_id -> most recent download ISO timestamp."""
    if not download_ids:
        return {}
    result: dict[str, str] = {}
    for d in download_repo.get_for_user(user_id, limit=10000, offset=0):
        if d.downloaded_at is None:
            continue
        aid = str(d.audiobook_id)
        ts_iso = d.downloaded_at.isoformat()
        if aid not in result or ts_iso > result[aid]:
            result[aid] = ts_iso
    return result


def _safe_int_ids(str_ids):
    """Convert string IDs to integers, skipping invalid values."""
    result = []
    for aid in str_ids:
        try:
            result.append(int(aid))
        except (ValueError, TypeError):  # fmt: skip
            continue
    return result


def _fetch_library_metadata(
    int_ids, history_ids, download_ids, position_ids, listened_map, downloaded_map
):
    """Fetch book metadata from library DB and build response list."""
    conn = _get_library_db()
    try:
        placeholders = ",".join("?" * len(int_ids))
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT id, title, author, duration_hours, cover_path, format "  # nosec B608  # noqa: S608
            f"FROM audiobooks WHERE id IN ({placeholders})",
            int_ids,
        )
        books = []
        for row in cursor.fetchall():
            rid = str(row["id"])
            books.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "author": row["author"],
                    "duration_hours": row["duration_hours"],
                    "cover_path": row["cover_path"],
                    "format": row["format"],
                    "has_history": rid in history_ids,
                    "has_download": rid in download_ids,
                    "has_position": rid in position_ids,
                    "last_listened_at": listened_map.get(rid),
                    "downloaded_at": downloaded_map.get(rid),
                }
            )
        return books
    finally:
        conn.close()


@user_bp.route("/library", methods=["GET"])
@login_required
def get_user_library():
    """
    Get distinct books the current user has positions, history, or downloads for.

    Cross-references auth DB data with library DB for metadata.
    """
    user = require_current_user()
    auth_db = get_auth_db()

    # Hidden books filtering
    hidden_repo = HiddenBookRepository(auth_db)
    hidden_ids = hidden_repo.get_hidden_ids(user.ensured_id)
    show_hidden = request.args.get("hidden", "").lower() == "true"

    # Collect all book IDs from user activity
    all_ids, history_ids, download_ids, position_ids = _collect_user_book_ids(auth_db, user.id)
    all_ids, hidden_count = _apply_hidden_filter(all_ids, hidden_ids, show_hidden)

    if not all_ids:
        return jsonify({"books": [], "total": 0, "hidden_count": hidden_count})

    # Build timestamp lookups
    history_repo = ListeningHistoryRepository(auth_db)
    download_repo = DownloadRepository(auth_db)
    listened_map = _build_last_listened_map(history_repo, user.id, history_ids)
    downloaded_map = _build_downloaded_at_map(download_repo, user.id, download_ids)

    # Fetch library metadata
    int_ids = _safe_int_ids(all_ids)
    if not int_ids:
        return jsonify({"books": [], "total": 0})

    books = _fetch_library_metadata(
        int_ids, history_ids, download_ids, position_ids, listened_map, downloaded_map
    )

    return jsonify({"books": books, "total": len(books), "hidden_count": hidden_count})


# ============================================================
# POST /api/user/library/hide — Hide books from My Library
# ============================================================


@user_bp.route("/library/hide", methods=["POST"])
@login_required
def hide_books():
    """Hide one or more books from the user's My Library view."""
    user = require_current_user()
    auth_db = get_auth_db()

    data = request.get_json(silent=True)
    if not data or "audiobook_ids" not in data:
        return jsonify({"error": "audiobook_ids required"}), 400

    ids = data["audiobook_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({"error": "audiobook_ids must be a list of integers"}), 400

    repo = HiddenBookRepository(auth_db)
    count = repo.hide(user.ensured_id, ids)
    return jsonify({"success": True, "hidden_count": count})


# ============================================================
# POST /api/user/library/unhide — Unhide books
# ============================================================


@user_bp.route("/library/unhide", methods=["POST"])
@login_required
def unhide_books():
    """Unhide one or more books, restoring them to My Library view."""
    user = require_current_user()
    auth_db = get_auth_db()

    data = request.get_json(silent=True)
    if not data or "audiobook_ids" not in data:
        return jsonify({"error": "audiobook_ids required"}), 400

    ids = data["audiobook_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({"error": "audiobook_ids must be a list of integers"}), 400

    repo = HiddenBookRepository(auth_db)
    count = repo.unhide(user.ensured_id, ids)
    return jsonify({"success": True, "unhidden_count": count})


# ============================================================
# GET /api/user/new-books — Books added after new_books_seen_at
# ============================================================


@user_bp.route("/new-books", methods=["GET"])
@login_required
def get_new_books():
    """
    Get books added to the library after the user's new_books_seen_at timestamp.

    If new_books_seen_at is NULL (first visit), returns ALL books.
    """
    user = require_current_user()
    auth_db = get_auth_db()

    prefs_repo = PreferencesRepository(auth_db)
    prefs = prefs_repo.get_or_create(user.ensured_id)

    conn = _get_library_db()
    try:
        cursor = conn.cursor()
        if prefs.new_books_seen_at is None:
            # Never dismissed — all books are "new"
            cursor.execute(
                "SELECT id, title, author, duration_hours,"
                " cover_path, format, created_at"
                " FROM audiobooks ORDER BY created_at DESC"
            )
        else:
            seen_at = prefs.new_books_seen_at.isoformat()
            cursor.execute(
                "SELECT id, title, author, duration_hours,"
                " cover_path, format, created_at"
                " FROM audiobooks WHERE created_at > ? ORDER BY created_at DESC",
                (seen_at,),
            )

        books = []
        for row in cursor.fetchall():
            books.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "author": row["author"],
                    "duration_hours": row["duration_hours"],
                    "cover_path": row["cover_path"],
                    "format": row["format"],
                    "created_at": row["created_at"],
                }
            )

        return jsonify(
            {
                "books": books,
                "total": len(books),
                "new_books_seen_at": (
                    prefs.new_books_seen_at.isoformat() if prefs.new_books_seen_at else None
                ),
            }
        )
    finally:
        conn.close()


# ============================================================
# POST /api/user/new-books/dismiss — Update new_books_seen_at
# ============================================================


@user_bp.route("/new-books/dismiss", methods=["POST"])
@login_required
def dismiss_new_books():
    """
    Mark all current books as "seen" by setting new_books_seen_at to now.
    """
    user = require_current_user()
    auth_db = get_auth_db()

    prefs_repo = PreferencesRepository(auth_db)
    prefs = prefs_repo.get_or_create(user.ensured_id)

    now = datetime.now()
    prefs.new_books_seen_at = now
    prefs.save(auth_db)

    return jsonify({"success": True, "new_books_seen_at": now.isoformat()})
