"""
Admin Activity API Module

Provides admin-only endpoints for viewing a unified activity log
(listening history + downloads) and aggregate statistics.

All endpoints require admin privileges (@admin_required).

Endpoints:
    GET /api/admin/activity       - Paginated, filterable activity log
    GET /api/admin/activity/stats - Aggregate activity statistics
"""

import sqlite3
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from .auth import admin_required, get_auth_db


def _rows_to_dicts(cursor) -> list[dict]:
    """Convert cursor results to list of dicts using column names from description.

    The auth DB uses sqlcipher3, whose connections do not accept sqlite3.Row
    as row_factory. This helper extracts column names from cursor.description
    and zips them with each row tuple to produce dicts with named access.
    """
    if cursor.description is None:
        return []
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# Blueprint for admin activity routes
admin_activity_bp = Blueprint("admin_activity", __name__, url_prefix="/api/admin")

# Module-level database path (library DB — set by init function)
_db_path: str | None = None


def init_admin_activity_routes(database_path: str) -> None:
    """Initialize admin activity routes with the library database path."""
    global _db_path
    _db_path = database_path


def _get_library_db() -> sqlite3.Connection:
    """Get library database connection."""
    if _db_path is None:
        raise RuntimeError(
            "Admin activity routes not initialized. "
            "Call init_admin_activity_routes first."
        )
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO date string, returning None if invalid or absent."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ============================================================
# GET /api/admin/activity — Paginated, filterable activity log
# ============================================================


def _parse_pagination(args):
    """Parse limit and offset from request args with defaults and bounds."""
    try:
        limit = max(1, min(int(args.get("limit", 50)), 200))
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(0, int(args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0
    return limit, offset


def _build_activity_filters(user_id, audiobook_id, from_date, to_date):
    """Build parallel WHERE clause components for listen and download subqueries.

    Returns (listen_wheres, listen_params, download_wheres, download_params).
    """
    listen_w, listen_p = [], []
    download_w, download_p = [], []

    _filter_pairs = [
        (user_id, "h.user_id = ?", "d.user_id = ?"),
        (audiobook_id, "h.audiobook_id = ?", "d.audiobook_id = ?"),
    ]
    for value, listen_clause, download_clause in _filter_pairs:
        if value is not None:
            listen_w.append(listen_clause)
            listen_p.append(value)
            download_w.append(download_clause)
            download_p.append(value)

    if from_date is not None:
        from_iso = from_date.isoformat()
        listen_w.append("h.started_at >= ?")
        listen_p.append(from_iso)
        download_w.append("d.downloaded_at >= ?")
        download_p.append(from_iso)

    if to_date is not None:
        to_boundary = (to_date + timedelta(days=1)).isoformat()
        listen_w.append("h.started_at < ?")
        listen_p.append(to_boundary)
        download_w.append("d.downloaded_at < ?")
        download_p.append(to_boundary)

    return listen_w, listen_p, download_w, download_p


def _build_union_sql(
    type_filter, listen_wheres, listen_params, download_wheres, download_params
):
    """Build the UNION ALL SQL and combined params.

    Returns (union_sql, all_params).
    """
    subqueries = []
    all_params: list = []

    if type_filter is None or type_filter == "listen":
        where = (" AND " + " AND ".join(listen_wheres)) if listen_wheres else ""
        subqueries.append(
            "SELECT 'listen' AS type, h.id, h.user_id, u.username, "  # nosec B608
            "h.audiobook_id, h.title AS stored_title, h.started_at AS timestamp, "
            "h.duration_listened_ms, NULL AS file_format "
            "FROM user_listening_history h "
            "JOIN users u ON h.user_id = u.id "
            f"WHERE 1=1{where}"
        )
        all_params.extend(listen_params)

    if type_filter is None or type_filter == "download":
        where = (" AND " + " AND ".join(download_wheres)) if download_wheres else ""
        subqueries.append(
            "SELECT 'download' AS type, d.id, d.user_id, u.username, "  # nosec B608
            "d.audiobook_id, d.title AS stored_title, d.downloaded_at AS timestamp, "
            "NULL AS duration_listened_ms, d.file_format "
            "FROM user_downloads d "
            "JOIN users u ON d.user_id = u.id "
            f"WHERE 1=1{where}"
        )
        all_params.extend(download_params)

    return " UNION ALL ".join(subqueries), all_params


def _row_to_activity_item(row, titles):
    """Convert a single activity row dict to an API response item."""
    aid = str(row["audiobook_id"])
    item = {
        "type": row["type"],
        "id": row["id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "audiobook_id": aid,
        "title": titles.get(aid) or row.get("stored_title"),
        "timestamp": row["timestamp"] or "",
    }
    if row["type"] == "listen":
        item["duration_listened_ms"] = row["duration_listened_ms"]
    else:
        item["file_format"] = row["file_format"]
    return item


@admin_activity_bp.route("/activity", methods=["GET"])
@admin_required
def get_activity():
    """
    Get a unified activity log combining listening and download events.

    Query params:
        limit:        Number of records (default 50, max 200)
        offset:       Pagination offset (default 0)
        user_id:      Filter by user ID
        type:         Filter by event type ("listen" or "download")
        audiobook_id: Filter by audiobook ID (must be numeric)
        from:         Start date (ISO format, inclusive)
        to:           End date (ISO format, inclusive — extended to next day)
    """
    limit, offset = _parse_pagination(request.args)

    # Parse filters
    user_id_filter = request.args.get("user_id", type=int)
    type_filter = request.args.get("type")
    audiobook_id_filter = request.args.get("audiobook_id")
    from_date = _parse_date(request.args.get("from"))
    to_date = _parse_date(request.args.get("to"))

    # Validate type filter
    if type_filter and type_filter not in ("listen", "download"):
        return jsonify({"activity": [], "total": 0, "limit": limit, "offset": offset})

    # Validate audiobook_id
    if audiobook_id_filter is not None:
        try:
            audiobook_id_filter = str(int(audiobook_id_filter))
        except (ValueError, TypeError):
            return jsonify({"error": "audiobook_id must be a number"}), 400

    # Build filters and SQL
    lw, lp, dw, dp = _build_activity_filters(
        user_id_filter, audiobook_id_filter, from_date, to_date
    )
    union_sql, all_params = _build_union_sql(type_filter, lw, lp, dw, dp)

    data_sql = f"{union_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    data_params = all_params + [limit, offset]
    count_sql = f"SELECT COUNT(*) FROM ({union_sql})"  # nosec B608

    auth_db = get_auth_db()
    with auth_db.connection() as conn:
        rows = _rows_to_dicts(conn.execute(data_sql, data_params))
        total = conn.execute(count_sql, list(all_params)).fetchone()[0]

    # Resolve titles from library DB
    book_ids = {str(row["audiobook_id"]) for row in rows}
    titles = _get_book_titles(book_ids)
    activity = [_row_to_activity_item(row, titles) for row in rows]

    return jsonify(
        {"activity": activity, "total": total, "limit": limit, "offset": offset}
    )


# ============================================================
# GET /api/admin/activity/stats — Aggregate activity statistics
# ============================================================


@admin_activity_bp.route("/activity/stats", methods=["GET"])
@admin_required
def get_activity_stats():
    """
    Get aggregate activity statistics.

    Returns:
        total_listens: Total number of listening sessions
        total_downloads: Total number of downloads
        active_users: Distinct users with any activity
        top_listened: Top 10 most-listened audiobooks [{audiobook_id, title, count}]
        top_downloaded: Top 10 most-downloaded audiobooks [{audiobook_id, title, count}]
    """
    auth_db = get_auth_db()

    with auth_db.connection() as conn:
        # Total listens
        total_listens = conn.execute(
            "SELECT COUNT(*) FROM user_listening_history"
        ).fetchone()[0]

        # Total downloads
        total_downloads = conn.execute(
            "SELECT COUNT(*) FROM user_downloads"
        ).fetchone()[0]

        # Active users (distinct users with any activity)
        active_users = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT user_id FROM user_listening_history "
            "  UNION "
            "  SELECT DISTINCT user_id FROM user_downloads"
            ")"
        ).fetchone()[0]

        # Top 10 most-listened audiobooks
        # MAX(title) picks a stored title from any row in the group
        top_listened_rows = _rows_to_dicts(
            conn.execute(
                "SELECT audiobook_id, MAX(title) AS stored_title, COUNT(*) AS cnt "
                "FROM user_listening_history "
                "GROUP BY audiobook_id "
                "ORDER BY cnt DESC "
                "LIMIT 10"
            )
        )

        # Top 10 most-downloaded audiobooks
        top_downloaded_rows = _rows_to_dicts(
            conn.execute(
                "SELECT audiobook_id, MAX(title) AS stored_title, COUNT(*) AS cnt "
                "FROM user_downloads "
                "GROUP BY audiobook_id "
                "ORDER BY cnt DESC "
                "LIMIT 10"
            )
        )

    # Collect all audiobook IDs for title lookup
    all_book_ids = set()
    for row in top_listened_rows:
        all_book_ids.add(row["audiobook_id"])
    for row in top_downloaded_rows:
        all_book_ids.add(row["audiobook_id"])

    # Look up titles from library DB (current), fall back to stored title
    titles = _get_book_titles(all_book_ids)

    top_listened = [
        {
            "audiobook_id": str(row["audiobook_id"]),
            "title": titles.get(str(row["audiobook_id"])) or row.get("stored_title"),
            "count": row["cnt"],
        }
        for row in top_listened_rows
    ]

    top_downloaded = [
        {
            "audiobook_id": str(row["audiobook_id"]),
            "title": titles.get(str(row["audiobook_id"])) or row.get("stored_title"),
            "count": row["cnt"],
        }
        for row in top_downloaded_rows
    ]

    return jsonify(
        {
            "total_listens": total_listens,
            "total_downloads": total_downloads,
            "active_users": active_users,
            "top_listened": top_listened,
            "top_downloaded": top_downloaded,
        }
    )


def _get_book_titles(audiobook_ids: set) -> dict[str, str | None]:
    """Look up audiobook titles from the library DB.

    Args:
        audiobook_ids: Set of audiobook ID strings from the auth DB.

    Returns:
        Mapping of audiobook_id (str) -> title (str or None).
    """
    if not audiobook_ids or _db_path is None:
        return {}

    # Convert to ints for library DB lookup
    int_ids = []
    for aid in audiobook_ids:
        try:
            int_ids.append(int(aid))
        except (ValueError, TypeError):
            continue

    if not int_ids:
        return {}

    try:
        conn = _get_library_db()
    except (RuntimeError, OSError):
        return {}

    try:
        placeholders = ",".join("?" * len(int_ids))
        cursor = conn.execute(
            f"SELECT id, title FROM audiobooks WHERE id IN ({placeholders})",  # nosec B608
            int_ids,
        )
        return {str(row["id"]): row["title"] for row in cursor.fetchall()}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
