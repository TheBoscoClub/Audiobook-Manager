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
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from .auth import admin_or_localhost, guest_allowed

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

        suffix = audio_path.suffix.lower()
        mime_map = {".opus": "audio/opus", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"}
        mimetype = mime_map.get(suffix, "audio/opus")
        return send_file(
            audio_path,
            mimetype=mimetype,
            as_attachment=False,
        )
    finally:
        conn.close()


@translated_audio_bp.route("/api/translated-audio/generate", methods=["POST"])
@admin_or_localhost
def generate_translated_audio():
    """Generate translated audio for audiobook chapters via TTS.

    Requires translated subtitles to exist first (reads VTT for text).
    Uses edge-tts by default (free, no API key).

    Request body:
        {
            "audiobook_id": 42,
            "locale": "zh-Hans",
            "voice": "zh-CN-XiaoxiaoNeural"
        }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    book_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    voice = data.get("voice", "zh-CN-XiaoxiaoNeural")

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

        # Verify translated subtitles exist
        sub_row = conn.execute(
            "SELECT vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if not sub_row:
            return jsonify({
                "error": "Translated subtitles not found. "
                         "Generate subtitles first via POST /api/subtitles/generate",
            }), 400

        # Check if translated audio already exists
        existing = conn.execute(
            "SELECT id FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if existing:
            return jsonify({
                "audiobook_id": book_id,
                "status": "exists",
                "message": "Translated audio already exists for this book.",
            })

        vtt_path = Path(sub_row["vtt_path"])
        audio_file_path = Path(book["file_path"])
    finally:
        conn.close()

    db_path = str(_db_path)

    def _generate():
        try:
            from ..localization.tts.edge_tts_provider import EdgeTTSProvider

            # Read translated text from VTT file
            if not vtt_path.exists():
                logger.error("VTT file missing: %s", vtt_path)
                return

            vtt_text = vtt_path.read_text(encoding="utf-8")
            # Extract text lines (skip WEBVTT header, timestamps, and cue numbers)
            lines = []
            for block in vtt_text.split("\n\n"):
                block_lines = block.strip().split("\n")
                for line in block_lines:
                    if (line.strip()
                            and not line.startswith("WEBVTT")
                            and "-->" not in line
                            and not line.strip().isdigit()):
                        lines.append(line.strip())

            if not lines:
                logger.error("No text found in VTT: %s", vtt_path)
                return

            # CJK languages don't use spaces between words/sentences
            lang_prefix = locale.split("-")[0].lower()
            joiner = "" if lang_prefix in ("zh", "ja", "ko") else " "
            full_text = joiner.join(lines)

            # Generate audio via edge-tts → MP3, then transcode to Opus
            output_dir = audio_file_path.parent / "translated"
            output_dir.mkdir(parents=True, exist_ok=True)
            mp3_path = output_dir / f"{audio_file_path.stem}.{locale}.tts.mp3"
            output_path = output_dir / f"{audio_file_path.stem}.{locale}.opus"

            tts = EdgeTTSProvider()
            tts.synthesize(full_text, locale, voice, mp3_path)

            # Transcode to Opus for consistency with the rest of the library
            import subprocess
            transcode = subprocess.run(
                ["ffmpeg", "-y", "-i", str(mp3_path), "-c:a", "libopus",
                 "-b:a", "64k", "-vbr", "on", str(output_path)],
                capture_output=True, text=True, timeout=300,
            )
            if transcode.returncode == 0:
                mp3_path.unlink(missing_ok=True)
            else:
                logger.warning("Opus transcode failed, keeping MP3: %s", transcode.stderr[:200])
                output_path = mp3_path

            # Get duration if possible
            duration = None
            try:
                import subprocess
                result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(output_path)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration = float(result.stdout.strip())
            except Exception:
                pass

            # Save to database
            gen_conn = sqlite3.connect(db_path)
            gen_conn.execute("PRAGMA journal_mode=WAL")
            gen_conn.execute("PRAGMA foreign_keys=ON")
            try:
                gen_conn.execute(
                    "INSERT OR REPLACE INTO chapter_translations_audio "
                    "(audiobook_id, chapter_index, locale, audio_path, "
                    " tts_provider, tts_voice, duration_seconds) "
                    "VALUES (?, 0, ?, ?, 'edge-tts', ?, ?)",
                    (book_id, locale, str(output_path), voice, duration),
                )
                gen_conn.commit()
                logger.info(
                    "Translated audio saved for book %d: %s (%.1fs)",
                    book_id, output_path.name, duration or 0,
                )
            finally:
                gen_conn.close()
        except Exception:
            logger.exception("TTS generation failed for book %d", book_id)

    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()

    return jsonify({
        "audiobook_id": book_id,
        "locale": locale,
        "voice": voice,
        "status": "started",
        "message": "Translated audio generation started in background.",
    })
