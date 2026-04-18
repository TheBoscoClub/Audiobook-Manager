"""
Supplement endpoints - PDF, ebook, and other companion files for audiobooks.
"""

from pathlib import Path

from flask import Blueprint, jsonify, send_file

from .auth import admin_if_enabled, download_permission_required, guest_allowed
from .core import FlaskResponse, get_db

supplements_bp = Blueprint("supplements", __name__)

# Module-level state set by init_supplements_routes()
_db_path: Path | None = None
_supplements_dir: Path | None = None

# Extension-to-type mapping for supplement files
_SUPPLEMENT_TYPE_MAP = {
    "pdf": "pdf",
    "epub": "ebook",
    "mobi": "ebook",
    "jpg": "image",
    "jpeg": "image",
    "png": "image",
    "mp3": "audio",
    "wav": "audio",
}

# MIME type mapping for supplement downloads
_MIME_TYPES = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "mp3": "audio/mpeg",
}


def _match_audiobook(cursor, file_path: Path) -> int | None:
    """Try to match a supplement file to an audiobook by title similarity."""
    clean_name = file_path.stem.replace("_", " ").replace("-", " ")
    pattern = f"%{clean_name[:30].lower()}%"

    cursor.execute(
        """
        SELECT id, title FROM audiobooks
        WHERE LOWER(title) LIKE ?
        OR LOWER(REPLACE(REPLACE(title, ':', ''), '-', '')) LIKE ?
        LIMIT 1
    """,
        (pattern, pattern),
    )

    match = cursor.fetchone()
    return match["id"] if match else None


def _process_supplement_file(file_path: Path, cursor, existing_paths: set[str]) -> str:
    """Process a single supplement file: insert or update in the database.

    Returns:
        "added", "updated", or "skipped"
    """
    path_str = str(file_path)
    ext = file_path.suffix.lower().lstrip(".")
    file_size = file_path.stat().st_size / (1024 * 1024)  # MB
    supplement_type = _SUPPLEMENT_TYPE_MAP.get(ext, "other")
    audiobook_id = _match_audiobook(cursor, file_path)

    if path_str in existing_paths:
        cursor.execute(
            """
            UPDATE supplements
            SET audiobook_id = ?, file_size_mb = ?, type = ?
            WHERE file_path = ?
        """,
            (audiobook_id, file_size, supplement_type, path_str),
        )
        return "updated"

    cursor.execute(
        """
        INSERT INTO supplements
            (audiobook_id, type, filename, file_path, file_size_mb)
        VALUES (?, ?, ?, ?, ?)
    """,
        (audiobook_id, supplement_type, file_path.name, path_str, file_size),
    )
    return "added"


@supplements_bp.route("/api/supplements", methods=["GET"])
@guest_allowed
def get_all_supplements() -> FlaskResponse:
    """Get all supplements in the library"""
    if _db_path is None:
        return jsonify({"error": "database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, a.title as audiobook_title, a.author as audiobook_author
        FROM supplements s
        LEFT JOIN audiobooks a ON s.audiobook_id = a.id
        ORDER BY s.filename COLLATE NOCASE
    """)

    supplements = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify({"supplements": supplements, "total": len(supplements)})


@supplements_bp.route("/api/supplements/stats", methods=["GET"])
@guest_allowed
def get_supplement_stats() -> FlaskResponse:
    """Get supplement statistics"""
    if _db_path is None:
        return jsonify({"error": "database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM supplements")
    total = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as linked FROM supplements WHERE audiobook_id IS NOT NULL")
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


@supplements_bp.route("/api/audiobooks/<int:audiobook_id>/supplements", methods=["GET"])
@guest_allowed
def get_audiobook_supplements(audiobook_id: int) -> FlaskResponse:
    """Get supplements for a specific audiobook"""
    if _db_path is None:
        return jsonify({"error": "database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM supplements WHERE audiobook_id = ?
        ORDER BY type COLLATE NOCASE, filename COLLATE NOCASE
    """,
        (audiobook_id,),
    )

    supplements = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(
        {"audiobook_id": audiobook_id, "supplements": supplements, "count": len(supplements)}
    )


@supplements_bp.route("/api/supplements/<int:supplement_id>/download", methods=["GET"])
@download_permission_required
def download_supplement(supplement_id: int) -> FlaskResponse:
    """Download/serve a supplement file"""
    if _db_path is None:
        return jsonify({"error": "database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM supplements WHERE id = ?", (supplement_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Supplement not found"}), 404

    file_path = Path(row["file_path"])
    if not file_path.exists():
        return jsonify({"error": "File not found on disk"}), 404

    ext = file_path.suffix.lower().lstrip(".")
    mimetype = _MIME_TYPES.get(ext, "application/octet-stream")

    return send_file(
        file_path, mimetype=mimetype, as_attachment=False, download_name=row["filename"]
    )


@supplements_bp.route("/api/supplements/scan", methods=["POST"])
@admin_if_enabled
def scan_supplements() -> FlaskResponse:
    """
    Scan the supplements directory and update the database.
    Links supplements to audiobooks by matching filenames to titles.
    """
    if _supplements_dir is None or not _supplements_dir.exists():
        return jsonify({"error": "Supplements directory not found"}), 404

    if _db_path is None:
        return jsonify({"error": "database not configured"}), 500
    conn = get_db(_db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT file_path FROM supplements")
    existing_paths = {row["file_path"] for row in cursor.fetchall()}

    added = []
    updated = []

    for file_path in _supplements_dir.iterdir():
        if not file_path.is_file():
            continue
        result = _process_supplement_file(file_path, cursor, existing_paths)
        if result == "added":
            added.append(file_path.name)
        elif result == "updated":
            updated.append(file_path.name)

    conn.commit()
    conn.close()

    return jsonify(
        {
            "success": True,
            "added": len(added),
            "updated": len(updated),
            "added_files": added[:20],
            "updated_files": updated[:20],
        }
    )


def init_supplements_routes(db_path, supplements_dir):
    """Initialize routes with database path and supplements directory."""
    global _db_path, _supplements_dir
    _db_path = db_path
    _supplements_dir = supplements_dir
    return supplements_bp
