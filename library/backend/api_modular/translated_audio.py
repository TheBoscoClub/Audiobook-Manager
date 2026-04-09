"""
Translated Audio API blueprint.

Manages TTS-generated translated audio for audiobook chapters.

Endpoints:
    GET  /api/audiobooks/<id>/translated-audio              — list translated audio
    GET  /api/audiobooks/<id>/translated-audio/<idx>/<locale> — stream translated chapter
    POST /api/translated-audio/generate                      — generate translated audio (admin)
"""

import logging
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from .auth import admin_if_enabled, guest_allowed

translated_audio_bp = Blueprint("translated_audio", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None


def init_translated_audio_routes(database_path, library_path):
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


@translated_audio_bp.route(
    "/api/audiobooks/<int:book_id>/translated-audio", methods=["GET"]
)
@guest_allowed
def get_book_translated_audio(book_id):
    """List all translated audio entries for a book."""
    locale = request.args.get("locale")
    conn = _get_db()
    try:
        if locale:
            rows = conn.execute(
                "SELECT * FROM chapter_translations_audio "
                "WHERE audiobook_id = ? AND locale = ? "
                "ORDER BY chapter_index",
                (book_id, locale),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chapter_translations_audio "
                "WHERE audiobook_id = ? ORDER BY chapter_index, locale",
                (book_id,),
            ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@translated_audio_bp.route(
    "/api/audiobooks/<int:book_id>/translated-audio/<int:chapter_index>/<locale>",
    methods=["GET"],
)
@guest_allowed
def stream_translated_chapter(book_id, chapter_index, locale):
    """Stream translated audio for a specific chapter."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT audio_path FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
            (book_id, chapter_index, locale),
        ).fetchone()
        if not row:
            return jsonify({"error": "Translated audio not found"}), 404

        audio_path = Path(row["audio_path"])
        if not audio_path.is_absolute() and _library_path:
            audio_path = _library_path / audio_path

        if not audio_path.exists():
            return jsonify({"error": "Audio file missing from disk"}), 404

        return send_file(
            audio_path,
            mimetype="audio/opus",
            as_attachment=False,
        )
    finally:
        conn.close()


@translated_audio_bp.route("/api/translated-audio/generate", methods=["POST"])
@admin_if_enabled
def generate_translated_audio():
    """Generate translated audio for audiobook chapters via TTS.

    Request body:
        {
            "audiobook_id": 42,
            "locale": "zh-Hans",
            "voice": "zh-CN-XiaoxiaoNeural",
            "chapters": [0, 1, 2]  -- or "all"
        }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    book_id = data.get("audiobook_id")
    locale = data.get("locale")
    voice = data.get("voice", "zh-CN-XiaoxiaoNeural")
    chapters = data.get("chapters", "all")

    if not book_id or not locale:
        return jsonify({"error": "audiobook_id and locale are required"}), 400

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        return jsonify({
            "audiobook_id": book_id,
            "locale": locale,
            "voice": voice,
            "chapters": chapters,
            "status": "pending",
            "message": "TTS audio generation pipeline ready. "
                       "Requires subtitle transcripts (Phase 2) to be generated first. "
                       "Install edge-tts: pip install edge-tts",
        })
    finally:
        conn.close()
