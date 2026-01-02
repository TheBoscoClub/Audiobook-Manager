"""
CRUD operations for audiobook records.
Handles create, update, delete, and query operations for individual and bulk records.
"""

from flask import Blueprint, Response, jsonify, request
from pathlib import Path

from .core import get_db, FlaskResponse

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
        ]
        updates = []
        values = []

        for field in allowed_fields:
            if field in data:
                updates.append(f"{field} = ?")
                values.append(data[field])

        if not updates:
            conn.close()
            return jsonify(
                {"success": False, "error": "No valid fields to update"}
            ), 400

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
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_crud_bp.route("/api/audiobooks/<int:id>", methods=["DELETE"])
    def delete_audiobook(id: int) -> FlaskResponse:
        """Delete audiobook from database (does not delete file)"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            # Delete related records first
            cursor.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM audiobook_topics WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (id,))
            cursor.execute("DELETE FROM supplements WHERE audiobook_id = ?", (id,))

            # Delete the audiobook
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (id,))
            rows_affected = cursor.rowcount
            conn.commit()
            conn.close()

            if rows_affected > 0:
                return jsonify({"success": True, "deleted": rows_affected})
            else:
                return jsonify({"success": False, "error": "Audiobook not found"}), 404
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_crud_bp.route("/api/audiobooks/bulk-update", methods=["POST"])
    def bulk_update_audiobooks() -> FlaskResponse:
        """Update a field for multiple audiobooks"""
        data = request.get_json()

        if not data or "ids" not in data or "field" not in data:
            return jsonify(
                {
                    "success": False,
                    "error": "Missing required fields: ids, field, value",
                }
            ), 400

        ids = data["ids"]
        field = data["field"]
        value = data.get("value")

        # Whitelist allowed fields for bulk update
        allowed_fields = ["narrator", "series", "publisher", "published_year"]
        if field not in allowed_fields:
            return jsonify(
                {
                    "success": False,
                    "error": f"Field not allowed for bulk update: {field}",
                }
            ), 400

        if not ids:
            return jsonify(
                {"success": False, "error": "No audiobook IDs provided"}
            ), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            placeholders = ",".join("?" * len(ids))
            query = f"UPDATE audiobooks SET {field} = ? WHERE id IN ({placeholders})"
            cursor.execute(query, [value] + ids)
            conn.commit()
            updated_count = cursor.rowcount
            conn.close()

            return jsonify({"success": True, "updated_count": updated_count})
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_crud_bp.route("/api/audiobooks/bulk-delete", methods=["POST"])
    def bulk_delete_audiobooks() -> FlaskResponse:
        """Delete multiple audiobooks"""
        data = request.get_json()

        if not data or "ids" not in data:
            return jsonify(
                {"success": False, "error": "Missing required field: ids"}
            ), 400

        ids = data["ids"]
        delete_files = data.get("delete_files", False)

        if not ids:
            return jsonify(
                {"success": False, "error": "No audiobook IDs provided"}
            ), 400

        conn = get_db(db_path)
        cursor = conn.cursor()

        try:
            # Get file paths if we need to delete files
            deleted_files = []
            if delete_files:
                placeholders = ",".join("?" * len(ids))
                cursor.execute(
                    f"SELECT id, file_path FROM audiobooks WHERE id IN ({placeholders})",
                    ids,
                )
                for row in cursor.fetchall():
                    file_path = Path(row["file_path"])
                    if file_path.exists():
                        try:
                            file_path.unlink()
                            deleted_files.append(str(file_path))
                        except Exception as e:
                            print(f"Warning: Could not delete file {file_path}: {e}")

            # Delete related records
            placeholders = ",".join("?" * len(ids))
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
            conn.commit()
            conn.close()

            return jsonify(
                {
                    "success": True,
                    "deleted_count": deleted_count,
                    "files_deleted": len(deleted_files) if delete_files else 0,
                }
            )
        except Exception as e:
            conn.close()
            return jsonify({"success": False, "error": str(e)}), 500

    @utilities_crud_bp.route("/api/audiobooks/missing-narrator", methods=["GET"])
    def get_audiobooks_missing_narrator() -> Response:
        """Get audiobooks without narrator information"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, author, narrator, series, file_path
            FROM audiobooks
            WHERE narrator IS NULL OR narrator = '' OR narrator = 'Unknown Narrator'
            ORDER BY title
            LIMIT 200
        """)

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(audiobooks)

    @utilities_crud_bp.route("/api/audiobooks/missing-hash", methods=["GET"])
    def get_audiobooks_missing_hash() -> Response:
        """Get audiobooks without SHA-256 hash"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, author, narrator, series, file_path
            FROM audiobooks
            WHERE sha256_hash IS NULL OR sha256_hash = ''
            ORDER BY title
            LIMIT 200
        """)

        audiobooks = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(audiobooks)

    return utilities_crud_bp
