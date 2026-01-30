"""
Supplement endpoints - PDF, ebook, and other companion files for audiobooks.
"""

from pathlib import Path

from flask import Blueprint, Response, jsonify, send_file

from .core import FlaskResponse, get_db
from .auth import auth_if_enabled, download_permission_required, admin_if_enabled

supplements_bp = Blueprint("supplements", __name__)


def init_supplements_routes(db_path, supplements_dir):
    """Initialize routes with database path and supplements directory."""

    @supplements_bp.route("/api/supplements", methods=["GET"])
    @auth_if_enabled
    def get_all_supplements() -> Response:
        """Get all supplements in the library"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT s.*, a.title as audiobook_title, a.author as audiobook_author
            FROM supplements s
            LEFT JOIN audiobooks a ON s.audiobook_id = a.id
            ORDER BY s.filename
        """
        )

        supplements = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({"supplements": supplements, "total": len(supplements)})

    @supplements_bp.route("/api/supplements/stats", methods=["GET"])
    @auth_if_enabled
    def get_supplement_stats() -> Response:
        """Get supplement statistics"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as total FROM supplements")
        total = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COUNT(*) as linked FROM supplements WHERE audiobook_id IS NOT NULL"
        )
        linked = cursor.fetchone()["linked"]

        cursor.execute("SELECT SUM(file_size_mb) as total_size FROM supplements")
        total_size = cursor.fetchone()["total_size"] or 0

        cursor.execute("SELECT type, COUNT(*) as count FROM supplements GROUP BY type")
        by_type = {row["type"]: row["count"] for row in cursor.fetchall()}

        conn.close()

        return jsonify(
            {
                "total_supplements": total,
                "linked_to_audiobooks": linked,
                "unlinked": total - linked,
                "total_size_mb": round(total_size, 2),
                "by_type": by_type,
            }
        )

    @supplements_bp.route(
        "/api/audiobooks/<int:audiobook_id>/supplements", methods=["GET"]
    )
    @auth_if_enabled
    def get_audiobook_supplements(audiobook_id: int) -> Response:
        """Get supplements for a specific audiobook"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM supplements WHERE audiobook_id = ?
            ORDER BY type, filename
        """,
            (audiobook_id,),
        )

        supplements = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(
            {
                "audiobook_id": audiobook_id,
                "supplements": supplements,
                "count": len(supplements),
            }
        )

    @supplements_bp.route(
        "/api/supplements/<int:supplement_id>/download", methods=["GET"]
    )
    @download_permission_required
    def download_supplement(supplement_id: int) -> FlaskResponse:
        """Download/serve a supplement file"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM supplements WHERE id = ?", (supplement_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({"error": "Supplement not found"}), 404

        file_path = Path(row["file_path"])
        if not file_path.exists():
            return jsonify({"error": "File not found on disk"}), 404

        # Map file types to MIME types
        mime_types = {
            "pdf": "application/pdf",
            "epub": "application/epub+zip",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "mp3": "audio/mpeg",
        }

        ext = file_path.suffix.lower().lstrip(".")
        mimetype = mime_types.get(ext, "application/octet-stream")

        return send_file(
            file_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=row["filename"],
        )

    @supplements_bp.route("/api/supplements/scan", methods=["POST"])
    @admin_if_enabled
    def scan_supplements() -> FlaskResponse:
        """
        Scan the supplements directory and update the database.
        Links supplements to audiobooks by matching filenames to titles.
        """
        if not supplements_dir.exists():
            return jsonify({"error": "Supplements directory not found"}), 404

        conn = get_db(db_path)
        cursor = conn.cursor()

        # Get existing supplements to avoid duplicates
        cursor.execute("SELECT file_path FROM supplements")
        existing_paths = {row["file_path"] for row in cursor.fetchall()}

        added = []
        updated = []

        for file_path in supplements_dir.iterdir():
            if file_path.is_file():
                path_str = str(file_path)
                filename = file_path.name
                ext = file_path.suffix.lower().lstrip(".")
                file_size = file_path.stat().st_size / (1024 * 1024)  # MB

                # Determine type
                type_map = {
                    "pdf": "pdf",
                    "epub": "ebook",
                    "mobi": "ebook",
                    "jpg": "image",
                    "jpeg": "image",
                    "png": "image",
                    "mp3": "audio",
                    "wav": "audio",
                }
                supplement_type = type_map.get(ext, "other")

                # Try to match to an audiobook by title
                # Clean filename for matching (remove extension, replace underscores)
                clean_name = file_path.stem.replace("_", " ").replace("-", " ")

                cursor.execute(
                    """
                    SELECT id, title FROM audiobooks
                    WHERE LOWER(title) LIKE ?
                    OR LOWER(REPLACE(REPLACE(title, ':', ''), '-', '')) LIKE ?
                    LIMIT 1
                """,
                    (f"%{clean_name[:30].lower()}%", f"%{clean_name[:30].lower()}%"),
                )

                match = cursor.fetchone()
                audiobook_id = match["id"] if match else None

                if path_str in existing_paths:
                    # Update existing record
                    cursor.execute(
                        """
                        UPDATE supplements
                        SET audiobook_id = ?, file_size_mb = ?, type = ?
                        WHERE file_path = ?
                    """,
                        (audiobook_id, file_size, supplement_type, path_str),
                    )
                    updated.append(filename)
                else:
                    # Insert new record
                    cursor.execute(
                        """
                        INSERT INTO supplements (audiobook_id, type, filename, file_path, file_size_mb)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (audiobook_id, supplement_type, filename, path_str, file_size),
                    )
                    added.append(filename)

        conn.commit()
        conn.close()

        return jsonify(
            {
                "success": True,
                "added": len(added),
                "updated": len(updated),
                "added_files": added[:20],  # Limit response size
                "updated_files": updated[:20],
            }
        )

    return supplements_bp
