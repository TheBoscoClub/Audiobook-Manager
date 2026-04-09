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
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from .auth import admin_or_localhost, guest_allowed

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
@admin_or_localhost
def generate_subtitles_endpoint():
    """Generate subtitles for an audiobook.

    Request body:
        {
            "audiobook_id": 42,
            "locale": "zh-Hans",       -- target translation locale
            "provider": ""             -- "deepl", "whisper", "local", or "" for auto
        }

    Single-file audiobooks use chapter_index 0.
    Generation runs in a background thread; returns immediately with status.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    book_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    provider_name = data.get("provider", "")

    if not book_id:
        return jsonify({"error": "audiobook_id is required"}), 400

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        audio_path = Path(book["file_path"])
        if not audio_path.exists():
            return jsonify({"error": "Audio file not found on disk"}), 404

        # Check if subtitles already exist for this book
        existing = conn.execute(
            "SELECT id FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND locale = ?",
            (book_id, "en"),
        ).fetchone()
        if existing:
            return jsonify({
                "audiobook_id": book_id,
                "status": "exists",
                "message": "Subtitles already exist for this book.",
            })
    finally:
        conn.close()

    # Run generation in background thread
    db_path = str(_db_path)

    def _generate():
        try:
            from ..localization.pipeline import generate_subtitles, get_stt_provider

            # Determine output directory (alongside the audio file)
            subtitle_dir = audio_path.parent / "subtitles"
            subtitle_dir.mkdir(parents=True, exist_ok=True)

            stt = get_stt_provider(provider_name)
            source_vtt, translated_vtt = generate_subtitles(
                audio_path=audio_path,
                output_dir=subtitle_dir,
                target_locale=locale,
                stt_provider=stt,
            )

            # Insert results into database
            gen_conn = sqlite3.connect(db_path)
            gen_conn.execute("PRAGMA journal_mode=WAL")
            gen_conn.execute("PRAGMA foreign_keys=ON")
            try:
                gen_conn.execute(
                    "INSERT OR REPLACE INTO chapter_subtitles "
                    "(audiobook_id, chapter_index, locale, vtt_path, "
                    " stt_provider, translation_provider) "
                    "VALUES (?, 0, 'en', ?, ?, NULL)",
                    (book_id, str(source_vtt), stt.name),
                )
                if translated_vtt:
                    gen_conn.execute(
                        "INSERT OR REPLACE INTO chapter_subtitles "
                        "(audiobook_id, chapter_index, locale, vtt_path, "
                        " stt_provider, translation_provider) "
                        "VALUES (?, 0, ?, ?, ?, 'deepl')",
                        (book_id, locale, str(translated_vtt), stt.name),
                    )
                gen_conn.commit()
                logger.info(
                    "Subtitles saved for book %d: %s%s",
                    book_id, source_vtt.name,
                    f", {translated_vtt.name}" if translated_vtt else "",
                )
            finally:
                gen_conn.close()
        except Exception:
            logger.exception("Subtitle generation failed for book %d", book_id)

    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()

    return jsonify({
        "audiobook_id": book_id,
        "locale": locale,
        "status": "started",
        "message": "Subtitle generation started in background.",
    })
