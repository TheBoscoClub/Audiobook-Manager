"""
CRUD operations for audiobook records.
Handles create, update, delete, and query operations for individual and bulk records.
"""

from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from .core import FlaskResponse, get_db

utilities_crud_bp = Blueprint("utilities_crud", __name__)


def init_crud_routes(db_path):
    """Initialize CRUD routes with database path."""

    @utilities_crud_bp.route("/api/audiobooks/<int:id>", methods=["PUT"])
    def update_audiobook(id: int) -> FlaskResponse:
        """Update audiobook metadata"""
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        # Build update query dynamically based on provided fields
        allowed_fields = [
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
        ]
        updates = []
        values = []

        for field in allowed_fields:
            if field in data:
                updates.append(f"{field} = ?")
                values.append(data[field])

        if not updates:
            conn.close()
            return (
                jsonify({"success": False, "error": "No valid fields to update"}),
                400,
            )

        values.append(id)
        query = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"

        try:
            cursor.execute(query, values)
            conn.commit()
            rows_affected = cursor.rowcount
            conn.close()

            if rows_affected > 0:
                return jsonify({"success": True, "updated": rows_affected})
            else:
                return jsonify({"success": False, "error": "Audiobook not found"}), 404
        except Exception:
            import logging

            logging.exception("Error updating audiobook %d", id)
            conn.close()
            return jsonify({"success": False, "error": "Database update failed"}), 500

    @utilities_crud_bp.route("/api/audiobooks/<int:id>", methods=["DELETE"])
    def delete_audiobook(id: int) -> FlaskResponse:
        """Delete audiobook from database (does not delete file)

        Uses transaction to ensure atomic deletion of audiobook and related records.
        """
        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            # Start explicit transaction for atomicity
            cursor.execute("BEGIN TRANSACTION")

            # Delete related records first
            cursor.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM audiobook_topics WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM supplements WHERE audiobook_id = ?", (id,))

            # Delete the audiobook
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (id,))
            rows_affected = cursor.rowcount

            # Commit only if audiobook was found and deleted
            if rows_affected > 0:
                conn.commit()
                conn.close()
                return jsonify({"success": True, "deleted": rows_affected})
            else:
                # Rollback the related record deletions if audiobook not found
                conn.rollback()
                conn.close()
                return jsonify({"success": False, "error": "Audiobook not found"}), 404
        except Exception:
            # Rollback on any error to prevent partial deletions
            import logging

            logging.exception("Error deleting audiobook %d", id)
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": "Database deletion failed"}), 500

    @utilities_crud_bp.route("/api/audiobooks/bulk-update", methods=["POST"])
    def bulk_update_audiobooks() -> FlaskResponse:
        """Update a field for multiple audiobooks"""
        data = request.get_json()

        if not data or "ids" not in data or "field" not in data:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Missing required fields: ids, field, value",
                    }
                ),
                400,
            )

        ids = data["ids"]
        field = data["field"]
        value = data.get("value")

        # Whitelist allowed fields for bulk update
        allowed_fields = [
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
        ]
        if field not in allowed_fields:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Field not allowed for bulk update: {field}",
                    }
                ),
                400,
            )

        if not ids:
            return (
                jsonify({"success": False, "error": "No audiobook IDs provided"}),
                400,
            )

        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            placeholders = ",".join("?" * len(ids))
            # CodeQL: field is validated against allowed_fields allowlist (line 156)
            query = f"UPDATE audiobooks SET {field} = ? WHERE id IN ({placeholders})"
            cursor.execute(query, [value] + ids)
            conn.commit()
            updated_count = cursor.rowcount
            conn.close()

            return jsonify({"success": True, "updated_count": updated_count})
        except Exception:
            import logging

            logging.exception("Error in bulk update")
            conn.close()
            return jsonify({"success": False, "error": "Bulk update failed"}), 500

    @utilities_crud_bp.route("/api/audiobooks/bulk-delete", methods=["POST"])
    def bulk_delete_audiobooks() -> FlaskResponse:
        """Delete multiple audiobooks.

        IMPORTANT: Database records are deleted FIRST in a transaction.
        Files are only deleted AFTER the database commit succeeds.
        This prevents orphaned files if DB deletion fails.
        """
        data = request.get_json()

        if not data or "ids" not in data:
            return (
                jsonify({"success": False, "error": "Missing required field: ids"}),
                400,
            )

        ids = data["ids"]
        delete_files = data.get("delete_files", False)

        if not ids:
            return (
                jsonify({"success": False, "error": "No audiobook IDs provided"}),
                400,
            )

        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            placeholders = ",".join("?" * len(ids))

            # STEP 1: Collect file paths BEFORE deletion (if we need to delete files)
            files_to_delete = []
            if delete_files:
                cursor.execute(
                    f"SELECT id, file_path FROM audiobooks WHERE id IN ({placeholders})",
                    ids,
                )
                files_to_delete = [
                    Path(row["file_path"])
                    for row in cursor.fetchall()
                    if row["file_path"]
                ]

            # STEP 2: Delete from database first (in transaction)
            cursor.execute("BEGIN TRANSACTION")

            # Delete related records
            cursor.execute(
                f"DELETE FROM audiobook_genres WHERE audiobook_id IN ({placeholders})",
                ids,
            )
            cursor.execute(
                f"DELETE FROM audiobook_topics WHERE audiobook_id IN ({placeholders})",
                ids,
            )
            cursor.execute(
                f"DELETE FROM audiobook_eras WHERE audiobook_id IN ({placeholders})",
                ids,
            )
            cursor.execute(
                f"DELETE FROM supplements WHERE audiobook_id IN ({placeholders})", ids
            )

            # Delete audiobooks
            cursor.execute(f"DELETE FROM audiobooks WHERE id IN ({placeholders})", ids)
            deleted_count = cursor.rowcount

            # Commit database changes first
            conn.commit()
            conn.close()

            # STEP 3: Only delete files AFTER successful DB commit
            # Files are recoverable; database records are not
            deleted_files = []
            failed_files = []
            if delete_files:
                for file_path in files_to_delete:
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            deleted_files.append(str(file_path))
                        except Exception:
                            # Log but don't fail - DB deletion succeeded
                            # CodeQL: Generic error avoids stack trace exposure
                            failed_files.append(
                                {"path": str(file_path), "error": "File deletion failed"}
                            )

            return jsonify(
                {
                    "success": True,
                    "deleted_count": deleted_count,
                    "files_deleted": len(deleted_files),
                    "files_failed": failed_files if failed_files else None,
                }
            )
        except Exception:
            # Rollback on any error
            import logging

            logging.exception("Error in bulk delete")
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "error": "Bulk deletion failed"}), 500

    @utilities_crud_bp.route("/api/audiobooks/missing-narrator", methods=["GET"])
    def get_audiobooks_missing_narrator() -> Response:
        """Get audiobooks without narrator information"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, title, author, narrator, series, file_path
            FROM audiobooks
            WHERE narrator IS NULL OR narrator = '' OR narrator = 'Unknown Narrator'
            ORDER BY title
            LIMIT 200
        """
        )

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(audiobooks)

    @utilities_crud_bp.route("/api/audiobooks/missing-hash", methods=["GET"])
    def get_audiobooks_missing_hash() -> Response:
        """Get audiobooks without SHA-256 hash"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, title, author, narrator, series, file_path
            FROM audiobooks
            WHERE sha256_hash IS NULL OR sha256_hash = ''
            ORDER BY title
            LIMIT 200
        """
        )

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(audiobooks)

    return utilities_crud_bp
