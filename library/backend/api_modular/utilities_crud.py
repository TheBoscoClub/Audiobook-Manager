"""
CRUD operations for audiobook records.
Handles create, update, delete, and query operations for individual and bulk records.
"""

import logging
import sys
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from .auth import admin_if_enabled, auth_if_enabled
from .core import FlaskResponse, get_db

# Import COVER_DIR for cover file cleanup on delete
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import COVER_DIR

utilities_crud_bp = Blueprint("utilities_crud", __name__)
logger = logging.getLogger(__name__)

# Module-level db_path, set once by init_crud_routes()
_db_path: Path = Path()

# Fields allowed for single-audiobook update
_UPDATE_ALLOWED_FIELDS = [
    "title",
    "author",
    "narrator",
    "publisher",
    "series",
    "series_sequence",
    "published_year",
    "asin",
    "isbn",
    "description",
    "content_type",
    "source",
    "edition",
    "acquired_date",
    # Enrichment fields
    "subtitle",
    "language",
    "format_type",
    "runtime_length_min",
    "release_date",
    "publisher_summary",
    "rating_overall",
    "rating_performance",
    "rating_story",
    "num_ratings",
    "num_reviews",
    "audible_image_url",
    "sample_url",
    "audible_sku",
    "is_adult_product",
    "merchandising_summary",
]

# Fields allowed for bulk update
_BULK_UPDATE_ALLOWED_FIELDS = [
    "narrator",
    "series",
    "publisher",
    "published_year",
    "content_type",
    "source",
    "edition",
    "acquired_date",
    "author",
    "asin",
    # Enrichment fields
    "subtitle",
    "language",
    "format_type",
    "release_date",
    "is_adult_product",
]

# Related tables to clean up when deleting audiobooks
# Maps table_name -> foreign key column name
_RELATED_TABLES = {
    "audiobook_genres": "audiobook_id",
    "audiobook_topics": "audiobook_id",
    "audiobook_eras": "audiobook_id",
    "editorial_reviews": "audiobook_id",
    "audible_categories": "audiobook_id",
    "book_authors": "book_id",
    "book_narrators": "book_id",
    "supplements": "audiobook_id",
}


# ── Helper functions ──────────────────────────────────────────────────


def _delete_related_records(cursor, ids, placeholders):
    """Delete all related records for given audiobook IDs across junction tables."""
    for table, col in _RELATED_TABLES.items():
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"DELETE FROM {table} WHERE {col} IN ({placeholders})", ids  # nosec B608  # noqa: S608
        )


def _cleanup_cover_files(cover_names):
    """Remove orphaned cover image files from disk."""
    for cover_name in cover_names:
        cover_file = COVER_DIR / cover_name
        if cover_file.is_file():
            cover_file.unlink(missing_ok=True)


def _delete_audiobook_files(file_paths):
    """Delete audiobook files from disk. Returns (deleted, failed) lists."""
    deleted_files = []
    failed_files = []
    for file_path in file_paths:
        if not file_path.exists():
            continue
        try:
            file_path.unlink()
            deleted_files.append(str(file_path))
        except Exception as e:
            # Log but don't fail - DB deletion already succeeded
            logger.warning("File deletion failed for %s: %s", file_path, e)
            failed_files.append({"path": str(file_path), "error": "File deletion failed"})
    return deleted_files, failed_files


def _collect_paths_for_deletion(cursor, ids, placeholders, delete_files):
    """Collect file and cover paths before deleting audiobook records."""
    files_to_delete = []
    covers_to_delete = []
    cursor.execute(
        "SELECT id, file_path, cover_path FROM audiobooks"  # nosec B608  # noqa: S608
        f" WHERE id IN ({placeholders})",
        ids,
    )
    for row in cursor.fetchall():
        if delete_files and row["file_path"]:
            files_to_delete.append(Path(row["file_path"]))
        if row["cover_path"]:
            covers_to_delete.append(row["cover_path"])
    return files_to_delete, covers_to_delete


def _validate_bulk_request(data, required_key, entity_label):
    """Validate common bulk request fields. Returns (ids, names, mode, error_response)."""
    if not data:
        return (None, None, None, (jsonify({"success": False, "error": "No data provided"}), 400))

    ids = data.get("ids", [])
    names = data.get(required_key, [])
    mode = data.get("mode", "add")
    if mode not in ("add", "remove"):
        return (
            None,
            None,
            None,
            (jsonify({"success": False, "error": "Invalid mode: must be 'add' or 'remove'"}), 400),
        )

    if not ids:
        return (
            None,
            None,
            None,
            (jsonify({"success": False, "error": "No audiobook IDs provided"}), 400),
        )
    if not names:
        return (
            None,
            None,
            None,
            (jsonify({"success": False, "error": f"No {entity_label} provided"}), 400),
        )
    if mode not in ("add", "remove"):
        return (
            None,
            None,
            None,
            (jsonify({"success": False, "error": "mode must be 'add' or 'remove'"}), 400),
        )
    return ids, names, mode, None


def _bulk_add_tags(cursor, ids, names, table, id_column, entity_table):
    """Add tag associations (genres/topics/eras) for multiple audiobooks."""
    affected = 0
    for name in names:
        name = name.strip()
        if not name:
            continue
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"INSERT OR IGNORE INTO {entity_table} (name) VALUES (?)", (name,)  # nosec B608  # noqa: S608
        )
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"SELECT id FROM {entity_table} WHERE name = ?", (name,)  # nosec B608  # noqa: S608
        )
        tag_id = cursor.fetchone()["id"]
        for book_id in ids:
            cursor.execute(
                f"INSERT OR IGNORE INTO {table}"  # nosec B608  # noqa: S608
                f" (audiobook_id, {id_column}) VALUES (?, ?)",
                (book_id, tag_id),
            )
            affected += cursor.rowcount
    return affected


def _bulk_remove_tags(cursor, ids, names, table, id_column, entity_table):
    """Remove tag associations (genres/topics/eras) for multiple audiobooks."""
    affected = 0
    placeholders = ",".join("?" * len(ids))
    for name in names:
        name = name.strip()
        if not name:
            continue
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"SELECT id FROM {entity_table} WHERE name = ?", (name,)  # nosec B608  # noqa: S608
        )
        row = cursor.fetchone()
        if not row:
            continue
        tag_id = row["id"]
        cursor.execute(
            f"DELETE FROM {table}"  # nosec B608  # noqa: S608
            f" WHERE {id_column} = ? AND audiobook_id IN ({placeholders})",
            [tag_id] + list(ids),
        )
        affected += cursor.rowcount
    return affected


def _set_tags_for_audiobook(cursor, audiobook_id, names, junction_table, id_column, entity_table):
    """Replace all tag associations for a single audiobook."""
    cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"DELETE FROM {junction_table} WHERE audiobook_id = ?", (audiobook_id,)  # nosec B608  # noqa: S608
    )
    for name in names:
        name = name.strip()
        if not name:
            continue
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"INSERT OR IGNORE INTO {entity_table} (name) VALUES (?)", (name,)  # nosec B608  # noqa: S608
        )
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"SELECT id FROM {entity_table} WHERE name = ?", (name,)  # nosec B608  # noqa: S608
        )
        tag_id = cursor.fetchone()["id"]
        cursor.execute(
            f"INSERT OR IGNORE INTO {junction_table}"  # nosec B608  # noqa: S608
            f" (audiobook_id, {id_column}) VALUES (?, ?)",
            (audiobook_id, tag_id),
        )


# ── Route handlers ────────────────────────────────────────────────────


@utilities_crud_bp.route("/api/audiobooks/<int:id>", methods=["PUT"])
@admin_if_enabled
def update_audiobook(id: int) -> FlaskResponse:
    """Update audiobook metadata"""
    data = request.get_json()

    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    updates = []
    values = []
    for field in _UPDATE_ALLOWED_FIELDS:
        if field in data:
            updates.append(f"{field} = ?")
            values.append(data[field])

    if not updates:
        return (jsonify({"success": False, "error": "No valid fields to update"}), 400)

    conn = get_db(_db_path)
    cursor = conn.cursor()
    values.append(id)
    query = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"  # nosec B608  # noqa: S608

    try:
        cursor.execute(
            query, values
        )  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        conn.commit()
        rows_affected = cursor.rowcount
        conn.close()

        if rows_affected > 0:
            return jsonify({"success": True, "updated": rows_affected})
        else:
            return jsonify({"success": False, "error": "Audiobook not found"}), 404
    except Exception:
        logger.exception("Error updating audiobook %d", id)
        conn.close()
        return jsonify({"success": False, "error": "Database update failed"}), 500


@utilities_crud_bp.route("/api/audiobooks/<int:id>", methods=["DELETE"])
@admin_if_enabled
def delete_audiobook(id: int) -> FlaskResponse:
    """Delete audiobook from database (does not delete file)

    Uses transaction to ensure atomic deletion of audiobook and related records.
    """
    conn = get_db(_db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN TRANSACTION")

        # Collect cover_path before deletion for cleanup
        cursor.execute("SELECT cover_path FROM audiobooks WHERE id = ?", (id,))
        row = cursor.fetchone()
        cover_to_delete = row["cover_path"] if row and row["cover_path"] else None

        # Delete related records first (all enrichment junction tables)
        _delete_related_records(cursor, (id,), "?")

        # Delete the audiobook
        cursor.execute("DELETE FROM audiobooks WHERE id = ?", (id,))
        rows_affected = cursor.rowcount

        if rows_affected > 0:
            conn.commit()
            conn.close()
            if cover_to_delete:
                _cleanup_cover_files([cover_to_delete])
            return jsonify({"success": True, "deleted": rows_affected})
        else:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": "Audiobook not found"}), 404
    except Exception:
        logger.exception("Error deleting audiobook %d", id)
        conn.rollback()
        conn.close()
        return jsonify({"success": False, "error": "Database deletion failed"}), 500


@utilities_crud_bp.route("/api/audiobooks/bulk-update", methods=["POST"])
@admin_if_enabled
def bulk_update_audiobooks() -> FlaskResponse:
    """Update a field for multiple audiobooks"""
    data = request.get_json()

    if not data or "ids" not in data or "field" not in data:
        return (
            jsonify({"success": False, "error": "Missing required fields: ids, field, value"}),
            400,
        )

    ids = data["ids"]
    field = data["field"]
    value = data.get("value")

    if field not in _BULK_UPDATE_ALLOWED_FIELDS:
        return (
            jsonify({"success": False, "error": f"Field not allowed for bulk update: {field}"}),
            400,
        )

    if not ids:
        return (jsonify({"success": False, "error": "No audiobook IDs provided"}), 400)

    conn = get_db(_db_path)
    cursor = conn.cursor()

    try:
        placeholders = ",".join("?" * len(ids))
        # CodeQL: field is validated against allowed_fields allowlist above
        query = f"UPDATE audiobooks SET {field} = ? WHERE id IN ({placeholders})"  # nosec B608  # nosemgrep: python.django.security.injection.tainted-sql-string.tainted-sql-string,python.flask.security.injection.tainted-sql-string.tainted-sql-string  # noqa: S608
        cursor.execute(
            query, [value] + ids
        )  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        conn.commit()
        updated_count = cursor.rowcount
        conn.close()

        return jsonify({"success": True, "updated_count": updated_count})
    except Exception as e:
        logger.exception("Error in bulk update: %s", e)
        conn.close()
        return jsonify({"success": False, "error": "Bulk update failed"}), 500


@utilities_crud_bp.route("/api/audiobooks/bulk-delete", methods=["POST"])
@admin_if_enabled
def bulk_delete_audiobooks() -> FlaskResponse:
    """Delete multiple audiobooks.

    IMPORTANT: Database records are deleted FIRST in a transaction.
    Files are only deleted AFTER the database commit succeeds.
    This prevents orphaned files if DB deletion fails.
    """
    data = request.get_json()

    if not data or "ids" not in data:
        return (jsonify({"success": False, "error": "Missing required field: ids"}), 400)

    ids = data["ids"]
    delete_files = data.get("delete_files", False)

    if not ids:
        return (jsonify({"success": False, "error": "No audiobook IDs provided"}), 400)

    conn = get_db(_db_path)
    cursor = conn.cursor()

    try:
        placeholders = ",".join("?" * len(ids))

        # STEP 1: Collect file paths and cover paths BEFORE deletion
        files_to_delete, covers_to_delete = _collect_paths_for_deletion(
            cursor, ids, placeholders, delete_files
        )

        # STEP 2: Delete from database first (in transaction)
        cursor.execute("BEGIN TRANSACTION")
        _delete_related_records(cursor, ids, placeholders)

        # Delete audiobooks
        cursor.execute(
            f"DELETE FROM audiobooks WHERE id IN ({placeholders})", ids  # noqa: S608 — placeholders built via '?'*len(ids); parameterized — no string injection  # nosec B608 — SQL — built from internal constants or allowlisted values; all user values use parameterized ? placeholders
        )  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query,python.django.security.injection.tainted-sql-string.tainted-sql-string,python.flask.security.injection.tainted-sql-string.tainted-sql-string
        deleted_count = cursor.rowcount

        # Commit database changes first
        conn.commit()
        conn.close()

        # STEP 3: Only delete files AFTER successful DB commit
        deleted_files, failed_files = [], []
        if delete_files:
            deleted_files, failed_files = _delete_audiobook_files(files_to_delete)

        # STEP 4: Clean up orphaned cover files
        _cleanup_cover_files(covers_to_delete)

        return jsonify(
            {
                "success": True,
                "deleted_count": deleted_count,
                "files_deleted": len(deleted_files),
                "files_failed": failed_files if failed_files else None,
            }
        )
    except Exception as e:
        logger.exception("Error in bulk delete: %s", e)
        conn.rollback()
        conn.close()
        return jsonify({"success": False, "error": "Bulk deletion failed"}), 500


@utilities_crud_bp.route("/api/audiobooks/missing-narrator", methods=["GET"])
@auth_if_enabled
def get_audiobooks_missing_narrator() -> Response:
    """Get audiobooks without narrator information"""
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, series, file_path
        FROM audiobooks
        WHERE narrator IS NULL OR narrator = '' OR narrator = 'Unknown Narrator'
        ORDER BY title COLLATE NOCASE
        LIMIT 200
    """)

    audiobooks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(audiobooks)


@utilities_crud_bp.route("/api/audiobooks/missing-hash", methods=["GET"])
@auth_if_enabled
def get_audiobooks_missing_hash() -> Response:
    """Get audiobooks without SHA-256 hash"""
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, series, file_path
        FROM audiobooks
        WHERE sha256_hash IS NULL OR sha256_hash = ''
        ORDER BY title COLLATE NOCASE
        LIMIT 200
    """)

    audiobooks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(audiobooks)


@utilities_crud_bp.route("/api/genres", methods=["GET"])
@auth_if_enabled
def list_genres() -> Response:
    """List all genres with book counts."""
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT g.id, g.name, COUNT(ag.audiobook_id) as book_count
        FROM genres g
        LEFT JOIN audiobook_genres ag ON g.id = ag.genre_id
        GROUP BY g.id, g.name
        ORDER BY g.name COLLATE NOCASE
    """)

    genres = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(genres)


@utilities_crud_bp.route("/api/audiobooks/<int:id>/genres", methods=["PUT"])
@admin_if_enabled
def set_audiobook_genres(id: int) -> FlaskResponse:
    """Set genres for a single audiobook (replaces all existing genres)."""
    data = request.get_json()

    if not data or "genres" not in data:
        return (jsonify({"success": False, "error": "Missing required field: genres"}), 400)

    genre_names = data["genres"]
    if not isinstance(genre_names, list):
        return (jsonify({"success": False, "error": "genres must be a list"}), 400)

    conn = get_db(_db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM audiobooks WHERE id = ?", (id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Audiobook not found"}), 404

        cursor.execute("BEGIN TRANSACTION")
        _set_tags_for_audiobook(cursor, id, genre_names, "audiobook_genres", "genre_id", "genres")
        conn.commit()
        conn.close()

        return jsonify({"success": True, "genres": genre_names})
    except Exception as e:
        logger.exception("Error setting genres for audiobook %d: %s", int(id), e)
        conn.rollback()
        conn.close()
        return (jsonify({"success": False, "error": "Failed to update genres"}), 500)


@utilities_crud_bp.route("/api/audiobooks/bulk-genres", methods=["POST"])
@admin_if_enabled
def bulk_manage_genres() -> FlaskResponse:
    """Add or remove genres for multiple audiobooks.

    Request body:
        ids: list of audiobook IDs
        genres: list of genre names
        mode: "add" or "remove"
    """
    data = request.get_json()
    ids, genre_names, mode, error = _validate_bulk_request(data, "genres", "genres")
    if error:
        return error

    conn = get_db(_db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN TRANSACTION")

        if mode == "add":
            affected = _bulk_add_tags(
                cursor, ids, genre_names, "audiobook_genres", "genre_id", "genres"
            )
        else:
            affected = _bulk_remove_tags(
                cursor, ids, genre_names, "audiobook_genres", "genre_id", "genres"
            )

        conn.commit()
        conn.close()

        return jsonify(
            {
                "success": True,
                "mode": mode,
                "affected": affected,
                "genre_count": len(genre_names),
                "book_count": len(ids),
            }
        )
    except Exception:
        logger.exception("Error in bulk genre %s", mode)
        conn.rollback()
        conn.close()
        return (jsonify({"success": False, "error": "Bulk genre operation failed"}), 500)


# ── Topic management ──────────────────────────────────────────────────


@utilities_crud_bp.route("/api/topics", methods=["GET"])
@auth_if_enabled
def list_topics() -> Response:
    """List all topics with book counts."""
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.name, COUNT(at.audiobook_id) as book_count
        FROM topics t
        LEFT JOIN audiobook_topics at ON t.id = at.topic_id
        GROUP BY t.id, t.name
        ORDER BY t.name COLLATE NOCASE
    """)
    topics = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(topics)


@utilities_crud_bp.route("/api/audiobooks/<int:id>/topics", methods=["PUT"])
@admin_if_enabled
def set_audiobook_topics(id: int) -> FlaskResponse:
    """Set topics for a single audiobook (replaces all existing topics)."""
    data = request.get_json()
    if not data or "topics" not in data:
        return jsonify({"success": False, "error": "Missing required field: topics"}), 400

    topic_names = data["topics"]
    if not isinstance(topic_names, list):
        return jsonify({"success": False, "error": "topics must be a list"}), 400

    conn = get_db(_db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM audiobooks WHERE id = ?", (id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Audiobook not found"}), 404

        cursor.execute("BEGIN TRANSACTION")
        _set_tags_for_audiobook(cursor, id, topic_names, "audiobook_topics", "topic_id", "topics")
        conn.commit()
        conn.close()
        return jsonify({"success": True, "topics": topic_names})
    except Exception as e:
        logger.exception("Error setting topics for audiobook %d: %s", int(id), e)
        conn.rollback()
        conn.close()
        return jsonify({"success": False, "error": "Failed to update topics"}), 500


@utilities_crud_bp.route("/api/audiobooks/bulk-topics", methods=["POST"])
@admin_if_enabled
def bulk_manage_topics() -> FlaskResponse:
    """Add or remove topics for multiple audiobooks."""
    data = request.get_json()
    ids, topic_names, mode, error = _validate_bulk_request(data, "topics", "topics")
    if error:
        return error

    conn = get_db(_db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")

        if mode == "add":
            affected = _bulk_add_tags(
                cursor, ids, topic_names, "audiobook_topics", "topic_id", "topics"
            )
        else:
            affected = _bulk_remove_tags(
                cursor, ids, topic_names, "audiobook_topics", "topic_id", "topics"
            )

        conn.commit()
        conn.close()
        return jsonify({"success": True, "mode": mode, "affected": affected})
    except Exception:
        logger.exception("Error in bulk topic %s", mode)
        conn.rollback()
        conn.close()
        return jsonify({"success": False, "error": "Bulk topic operation failed"}), 500


# ── Era management ────────────────────────────────────────────────────


@utilities_crud_bp.route("/api/eras", methods=["GET"])
@auth_if_enabled
def list_eras() -> Response:
    """List all eras with book counts."""
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.id, e.name, COUNT(ae.audiobook_id) as book_count
        FROM eras e
        LEFT JOIN audiobook_eras ae ON e.id = ae.era_id
        GROUP BY e.id, e.name
        ORDER BY e.name COLLATE NOCASE
    """)
    eras = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(eras)


@utilities_crud_bp.route("/api/audiobooks/<int:id>/eras", methods=["PUT"])
@admin_if_enabled
def set_audiobook_eras(id: int) -> FlaskResponse:
    """Set eras for a single audiobook (replaces all existing eras)."""
    data = request.get_json()
    if not data or "eras" not in data:
        return jsonify({"success": False, "error": "Missing required field: eras"}), 400

    era_names = data["eras"]
    if not isinstance(era_names, list):
        return jsonify({"success": False, "error": "eras must be a list"}), 400

    conn = get_db(_db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM audiobooks WHERE id = ?", (id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Audiobook not found"}), 404

        cursor.execute("BEGIN TRANSACTION")
        _set_tags_for_audiobook(cursor, id, era_names, "audiobook_eras", "era_id", "eras")
        conn.commit()
        conn.close()
        return jsonify({"success": True, "eras": era_names})
    except Exception as e:
        logger.exception("Error setting eras for audiobook %d: %s", int(id), e)
        conn.rollback()
        conn.close()
        return jsonify({"success": False, "error": "Failed to update eras"}), 500


# ── Editorial reviews ─────────────────────────────────────────────────


@utilities_crud_bp.route("/api/audiobooks/<int:id>/reviews", methods=["GET"])
@auth_if_enabled
def get_editorial_reviews(id: int) -> Response:
    """Get editorial reviews for an audiobook."""
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, review_text, source FROM editorial_reviews WHERE audiobook_id = ? ORDER BY id",
        (id,),
    )
    reviews = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(reviews)


@utilities_crud_bp.route("/api/audiobooks/<int:id>/reviews", methods=["POST"])
@admin_if_enabled
def add_editorial_review(id: int) -> FlaskResponse:
    """Add an editorial review to an audiobook."""
    data = request.get_json()
    if not data or "review_text" not in data:
        return jsonify({"success": False, "error": "Missing review_text"}), 400

    conn = get_db(_db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM audiobooks WHERE id = ?", (id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Audiobook not found"}), 404
        cursor.execute(
            "INSERT INTO editorial_reviews (audiobook_id, review_text, source) VALUES (?, ?, ?)",
            (id, data["review_text"], data.get("source", "")),
        )
        conn.commit()
        review_id = cursor.lastrowid
        conn.close()
        return jsonify({"success": True, "id": review_id})
    except Exception as e:
        logger.exception("Error adding review for audiobook %d: %s", int(id), e)
        conn.close()
        return jsonify({"success": False, "error": "Failed to add review"}), 500


@utilities_crud_bp.route("/api/reviews/<int:review_id>", methods=["DELETE"])
@admin_if_enabled
def delete_editorial_review(review_id: int) -> FlaskResponse:
    """Delete an editorial review."""
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM editorial_reviews WHERE id = ?", (review_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Review not found"}), 404


# ── Audible categories ────────────────────────────────────────────────


@utilities_crud_bp.route("/api/audiobooks/<int:id>/categories", methods=["GET"])
@auth_if_enabled
def get_audible_categories(id: int) -> Response:
    """Get Audible categories for an audiobook."""
    conn = get_db(_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, category_path, category_name, root_category, depth,"
        " audible_category_id"
        " FROM audible_categories WHERE audiobook_id = ? ORDER BY depth, category_name COLLATE NOCASE",
        (id,),
    )
    categories = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(categories)


# ── Enrichment summary ────────────────────────────────────────────────


@utilities_crud_bp.route("/api/audiobooks/enrichment-stats", methods=["GET"])
@auth_if_enabled
def get_enrichment_stats() -> Response:
    """Get enrichment coverage statistics."""
    conn = get_db(_db_path)
    cursor = conn.cursor()

    stats = {}
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    stats["total"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE audible_enriched_at IS NOT NULL")
    stats["audible_enriched"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE isbn_enriched_at IS NOT NULL")
    stats["isbn_enriched"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(DISTINCT ag.audiobook_id) FROM audiobook_genres ag
    """)
    stats["with_genres"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT audiobook_id) FROM audiobook_topics")
    stats["with_topics"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT audiobook_id) FROM audiobook_eras")
    stats["with_eras"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT audiobook_id) FROM editorial_reviews")
    stats["with_reviews"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT audiobook_id) FROM audible_categories")
    stats["with_categories"] = cursor.fetchone()[0]

    cursor.execute(
        "SELECT content_type, COUNT(*) as count FROM audiobooks GROUP BY content_type ORDER BY count DESC"
    )
    stats["content_types"] = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE subtitle IS NOT NULL AND subtitle != ''")
    stats["with_subtitle"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE language IS NOT NULL AND language != ''")
    stats["with_language"] = cursor.fetchone()[0]

    conn.close()
    return jsonify(stats)


# ── Initialization ────────────────────────────────────────────────────


def init_crud_routes(db_path):
    """Initialize CRUD routes with database path."""
    global _db_path
    _db_path = db_path
    return utilities_crud_bp
