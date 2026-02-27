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
    GET  /api/user/new-books            - Books added after user's last seen timestamp
    POST /api/user/new-books/dismiss    - Update new_books_seen_at preference
"""

import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request

from .auth import get_auth_db, get_current_user, login_required

# Import auth models for per-user state
from auth import (
    DownloadRepository,
    ListeningHistoryRepository,
    PositionRepository,
    PreferencesRepository,
    UserDownload,
)

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
        raise RuntimeError(
            "User state routes not initialized. Call init_user_state_routes first."
        )
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
    user = get_current_user()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0

    auth_db = get_auth_db()
    repo = ListeningHistoryRepository(auth_db)
    items = repo.get_for_user(user.id, limit=limit, offset=offset)

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
    user = get_current_user()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0

    auth_db = get_auth_db()
    repo = DownloadRepository(auth_db)
    items = repo.get_for_user(user.id, limit=limit, offset=offset)

    return jsonify(
        {
            "items": [
                {
                    "id": d.id,
                    "audiobook_id": d.audiobook_id,
                    "downloaded_at": d.downloaded_at.isoformat()
                    if d.downloaded_at
                    else None,
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
    user = get_current_user()

    # Verify the audiobook exists and get title for denormalized storage
    conn = _get_library_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title FROM audiobooks WHERE id = ?", (audiobook_id,)
        )
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
        user_id=user.id,
        audiobook_id=str(audiobook_id),
        title=book_title,
        file_format=file_format,
    )
    download.save(auth_db)

    return jsonify(
        {
            "success": True,
            "download_id": download.id,
            "audiobook_id": audiobook_id,
        }
    )


# ============================================================
# GET /api/user/library — Distinct books user has interacted with
# ============================================================


@user_bp.route("/library", methods=["GET"])
@login_required
def get_user_library():
    """
    Get distinct books the current user has positions, history, or downloads for.

    Cross-references auth DB data with library DB for metadata.
    """
    user = get_current_user()
    auth_db = get_auth_db()

    # Collect unique audiobook IDs from all user activity sources
    history_repo = ListeningHistoryRepository(auth_db)
    download_repo = DownloadRepository(auth_db)

    history_ids = set(history_repo.get_user_book_ids(user.id))

    # Get download IDs (uses efficient DISTINCT query)
    download_book_ids = set(download_repo.get_user_book_ids(user.id))

    # Get position IDs
    position_repo = PositionRepository(auth_db)
    positions = position_repo.get_all_for_user(user.id)
    position_ids = {str(p.audiobook_id) for p in positions}

    # Merge all IDs
    all_ids = history_ids | download_book_ids | position_ids

    if not all_ids:
        return jsonify({"books": [], "total": 0})

    # Build timestamp lookup dicts for history and downloads
    # last_listened_at: most recent ended_at (or started_at) per audiobook
    last_listened_map: dict[str, str] = {}
    if history_ids:
        history_items = history_repo.get_for_user(user.id, limit=10000, offset=0)
        for h in history_items:
            ts = h.ended_at or h.started_at
            if ts is None:
                continue
            aid = str(h.audiobook_id)
            if aid not in last_listened_map or ts.isoformat() > last_listened_map[aid]:
                last_listened_map[aid] = ts.isoformat()

    # downloaded_at: most recent download per audiobook
    downloaded_at_map: dict[str, str] = {}
    if download_book_ids:
        download_items = download_repo.get_for_user(user.id, limit=10000, offset=0)
        for d in download_items:
            if d.downloaded_at is None:
                continue
            aid = str(d.audiobook_id)
            if (
                aid not in downloaded_at_map
                or d.downloaded_at.isoformat() > downloaded_at_map[aid]
            ):
                downloaded_at_map[aid] = d.downloaded_at.isoformat()

    # Fetch metadata from library DB
    conn = _get_library_db()
    try:
        # Build parameterized query for integer IDs
        int_ids = []
        for aid in all_ids:
            try:
                int_ids.append(int(aid))
            except (ValueError, TypeError):
                continue

        if not int_ids:
            return jsonify({"books": [], "total": 0})

        placeholders = ",".join("?" * len(int_ids))
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT id, title, author, duration_hours, cover_path, format "
            f"FROM audiobooks WHERE id IN ({placeholders})",
            int_ids,
        )

        books = []
        for row in cursor.fetchall():
            row_id_str = str(row["id"])
            books.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "author": row["author"],
                    "duration_hours": row["duration_hours"],
                    "cover_path": row["cover_path"],
                    "format": row["format"],
                    "has_history": row_id_str in history_ids,
                    "has_download": row_id_str in download_book_ids,
                    "has_position": row_id_str in position_ids,
                    "last_listened_at": last_listened_map.get(row_id_str),
                    "downloaded_at": downloaded_at_map.get(row_id_str),
                }
            )

        return jsonify({"books": books, "total": len(books)})
    finally:
        conn.close()


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
    user = get_current_user()
    auth_db = get_auth_db()

    prefs_repo = PreferencesRepository(auth_db)
    prefs = prefs_repo.get_or_create(user.id)

    conn = _get_library_db()
    try:
        cursor = conn.cursor()
        if prefs.new_books_seen_at is None:
            # Never dismissed — all books are "new"
            cursor.execute(
                "SELECT id, title, author, duration_hours, cover_path, format, created_at "
                "FROM audiobooks ORDER BY created_at DESC"
            )
        else:
            seen_at = prefs.new_books_seen_at.isoformat()
            cursor.execute(
                "SELECT id, title, author, duration_hours, cover_path, format, created_at "
                "FROM audiobooks WHERE created_at > ? ORDER BY created_at DESC",
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
                "new_books_seen_at": prefs.new_books_seen_at.isoformat()
                if prefs.new_books_seen_at
                else None,
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
    user = get_current_user()
    auth_db = get_auth_db()

    prefs_repo = PreferencesRepository(auth_db)
    prefs = prefs_repo.get_or_create(user.id)

    now = datetime.now()
    prefs.new_books_seen_at = now
    prefs.save(auth_db)

    return jsonify(
        {
            "success": True,
            "new_books_seen_at": now.isoformat(),
        }
    )
