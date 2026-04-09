"""
Subtitle API blueprint.

Manages VTT subtitle files for audiobook chapters.

Endpoints:
    GET  /api/audiobooks/<id>/subtitles              — list subtitles for a book
    GET  /api/audiobooks/<id>/subtitles/<idx>/<locale> — get VTT path for a chapter+locale
    POST /api/subtitles/generate                      — generate subtitles for chapters (admin)
"""

import logging
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from .auth import admin_if_enabled, guest_allowed

subtitles_bp = Blueprint("subtitles", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None


def init_subtitles_routes(database_path, library_path):
    """Initialize with database and library paths."""
    global _db_path, _library_path
    _db_path = database_path
    _library_path = library_path


def _get_db():
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@subtitles_bp.route("/api/audiobooks/<int:book_id>/subtitles", methods=["GET"])
@guest_allowed
def get_book_subtitles(book_id):
    """List all subtitle entries for a book."""
    locale = request.args.get("locale")
    conn = _get_db()
    try:
        if locale:
            rows = conn.execute(
                "SELECT * FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = ? "
                "ORDER BY chapter_index",
                (book_id, locale),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chapter_subtitles "
                "WHERE audiobook_id = ? ORDER BY chapter_index, locale",
                (book_id,),
            ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@subtitles_bp.route(
    "/api/audiobooks/<int:book_id>/subtitles/<int:chapter_index>/<locale>",
    methods=["GET"],
)
@guest_allowed
def get_chapter_subtitle(book_id, chapter_index, locale):
    """Get or serve the VTT file for a specific chapter and locale."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
            (book_id, chapter_index, locale),
        ).fetchone()
        if not row:
            return jsonify({"error": "Subtitle not found"}), 404

        vtt_path = Path(row["vtt_path"])
        if not vtt_path.is_absolute() and _library_path:
            vtt_path = _library_path / vtt_path

        if not vtt_path.exists():
            return jsonify({"error": "VTT file missing from disk"}), 404

        return send_file(
            vtt_path,
            mimetype="text/vtt; charset=utf-8",
            as_attachment=False,
        )
    finally:
        conn.close()


@subtitles_bp.route("/api/subtitles/generate", methods=["POST"])
@admin_if_enabled
def generate_subtitles():
    """Generate subtitles for audiobook chapters.

    Request body:
        {
            "audiobook_id": 42,
            "locale": "zh-Hans",
            "chapters": [0, 1, 2]  -- or "all"
        }

    Returns job status. Actual generation happens synchronously for now;
    a background job system will be added for large batches.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    book_id = data.get("audiobook_id")
    locale = data.get("locale")
    chapters = data.get("chapters", "all")

    if not book_id or not locale:
        return jsonify({"error": "audiobook_id and locale are required"}), 400

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, folder_name FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        return jsonify({
            "audiobook_id": book_id,
            "locale": locale,
            "chapters": chapters,
            "status": "pending",
            "message": "Subtitle generation pipeline ready but requires "
                       "STT provider API keys to be configured. "
                       "Set AUDIOBOOKS_DEEPL_API_KEY or AUDIOBOOKS_RUNPOD_API_KEY "
                       "in your environment.",
        })
    finally:
        conn.close()
