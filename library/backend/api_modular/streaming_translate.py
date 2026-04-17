"""
Streaming translation API blueprint.

Provides real-time, on-demand translation triggered by playback.
The player requests translation for a chapter; the coordinator
dispatches chapter-level work to GPU workers and streams segment
completion events back via WebSocket.

Endpoints:
    POST /api/translate/stream           — request streaming translation for a book
    GET  /api/translate/segments/<id>/<ch>/<locale> — segment bitmap for a chapter
    GET  /api/translate/session/<id>/<locale>       — current streaming session state
    POST /api/translate/warmup           — pre-warm GPU on app open
    POST /api/translate/seek             — handle seek to uncached position
"""

import logging
import re
import sqlite3
from pathlib import Path

from flask import Blueprint, g, jsonify, request

from .auth import guest_allowed
from .websocket import connection_manager

streaming_bp = Blueprint("streaming_translate", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None

SEGMENT_DURATION_SEC = 30
BUFFER_THRESHOLD = 6  # 3 minutes = 6 segments

# Allowed locale patterns for path/log safety
_SAFE_LOCALE_RE = re.compile(r"^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$")

# Control character stripper for log messages (CRLF injection / log forging defense)
_LOG_SCRUB_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _safe_log_value(value) -> str:
    """Sanitize a value for safe inclusion in log messages.

    Strips CR, LF, null bytes, and other control characters that could be
    used for log forging (CRLF injection). Truncates overly long values.
    """
    s = str(value) if value is not None else ""
    s = _LOG_SCRUB_RE.sub("_", s)
    if len(s) > 200:
        s = s[:200] + "...(truncated)"
    return s


def _sanitize_locale(locale: str) -> str:
    """Validate locale string — reject path traversal and log injection."""
    if not isinstance(locale, str) or not _SAFE_LOCALE_RE.match(locale):
        raise ValueError(f"invalid locale: {locale!r}")
    return locale


def _safe_subtitles_path(
    library_root: Path, audiobook_id: int, chapter_index: int, locale: str
) -> Path:
    """Build a VTT subtitle path and confirm it is inside `library_root`.

    `audiobook_id` and `chapter_index` must be ints; `locale` must already
    have been validated by `_sanitize_locale`. This function raises
    `ValueError` if the resolved path escapes the library root (defense in
    depth against path injection — CodeQL py/path-injection).
    """
    if not isinstance(audiobook_id, int) or audiobook_id < 0:
        raise ValueError(f"invalid audiobook_id: {audiobook_id!r}")
    if not isinstance(chapter_index, int) or chapter_index < 0:
        raise ValueError(f"invalid chapter_index: {chapter_index!r}")
    # Re-validate locale (belt-and-suspenders) to ensure no traversal chars
    _sanitize_locale(locale)

    root = library_root.resolve()
    subtitles_dir = (root / "subtitles" / str(audiobook_id)).resolve()
    # Python 3.9+: Path.is_relative_to
    if not subtitles_dir.is_relative_to(root):
        raise ValueError("resolved subtitles dir escapes library root")

    vtt_path = (subtitles_dir / f"ch{chapter_index:03d}.{locale}.vtt").resolve()
    if not vtt_path.is_relative_to(root):
        raise ValueError("resolved VTT path escapes library root")
    return vtt_path


def _get_db():
    """Get database connection for this request."""
    db = getattr(g, "_streaming_db", None)
    if db is None:
        db = sqlite3.connect(str(_db_path))
        db.row_factory = sqlite3.Row
        g._streaming_db = db
    return db


def _close_db(exc=None):
    db = getattr(g, "_streaming_db", None)
    if db is not None:
        db.close()


def _has_cached_subtitles(
    db, audiobook_id: int, chapter_index: int, locale: str
) -> bool:
    """Check if full chapter subtitles already exist (from batch pipeline)."""
    row = db.execute(
        "SELECT id FROM chapter_subtitles "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    return row is not None


def _has_cached_audio(db, audiobook_id: int, chapter_index: int, locale: str) -> bool:
    """Check if translated audio already exists for a chapter."""
    row = db.execute(
        "SELECT id FROM chapter_translations_audio "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    return row is not None


def _get_chapter_count(db, audiobook_id: int) -> int:
    """Get total number of chapters for a book from existing subtitles or audio data."""
    row = db.execute(
        "SELECT MAX(chapter_index) + 1 as cnt FROM chapter_subtitles "
        "WHERE audiobook_id = ?",
        (audiobook_id,),
    ).fetchone()
    if row and row["cnt"]:
        return row["cnt"]
    # Fallback: check translation queue for chapter count
    row = db.execute(
        "SELECT total_chapters FROM translation_queue WHERE audiobook_id = ?",
        (audiobook_id,),
    ).fetchone()
    if row and row["total_chapters"]:
        return row["total_chapters"]
    return 0


def _get_book_duration_sec(db, audiobook_id: int) -> float:
    """Get book duration in seconds."""
    row = db.execute(
        "SELECT duration_hours FROM audiobooks WHERE id = ?", (audiobook_id,)
    ).fetchone()
    if row and row["duration_hours"]:
        return row["duration_hours"] * 3600
    return 0


def _chapter_segment_count(duration_sec: float) -> int:
    """Calculate number of 30-second segments for a given duration."""
    if duration_sec <= 0:
        return 0
    import math

    return math.ceil(duration_sec / SEGMENT_DURATION_SEC)


def _get_chapter_duration_sec(db, audiobook_id: int, chapter_index: int) -> float:
    """Estimate chapter duration from book duration and chapter count.

    For more accurate results, the streaming worker uses ffprobe chapter
    metadata directly. This estimate is used to pre-populate segment rows.
    """
    book_dur = _get_book_duration_sec(db, audiobook_id)
    chapter_count = _get_chapter_count(db, audiobook_id) or 1
    return book_dur / chapter_count


def _ensure_chapter_segments(
    db, audiobook_id: int, chapter_index: int, locale: str, priority: int = 1
) -> int:
    """Create pending segment rows for a chapter if they don't exist.

    Returns the number of segments (existing or newly created).
    """
    existing = db.execute(
        "SELECT COUNT(*) as cnt FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
        (audiobook_id, chapter_index, locale),
    ).fetchone()

    if existing and existing["cnt"] > 0:
        return existing["cnt"]

    ch_duration = _get_chapter_duration_sec(db, audiobook_id, chapter_index)
    seg_count = _chapter_segment_count(ch_duration)

    if seg_count <= 0:
        # Fallback: at least estimate from total book duration / 30s
        book_dur = _get_book_duration_sec(db, audiobook_id)
        chapter_count = _get_chapter_count(db, audiobook_id) or 1
        seg_count = max(1, _chapter_segment_count(book_dur / chapter_count))

    for seg_idx in range(seg_count):
        db.execute(
            "INSERT OR IGNORE INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (audiobook_id, chapter_index, seg_idx, locale, priority),
        )
    db.commit()

    logger.info(
        "Created %d segment rows: book=%d ch=%d locale=%s priority=%d",
        seg_count,
        audiobook_id,
        chapter_index,
        _safe_log_value(locale),
        priority,
    )
    return seg_count


def _get_segment_bitmap(db, audiobook_id: int, chapter_index: int, locale: str) -> dict:
    """Get segment completion bitmap for a chapter.

    Returns:
        dict with 'completed' (list of segment indices),
        'total' (total segments), 'all_cached' (bool).
    """
    # Check if the full chapter is already cached from batch pipeline
    if _has_cached_subtitles(db, audiobook_id, chapter_index, locale):
        return {"completed": "all", "total": 0, "all_cached": True}

    rows = db.execute(
        "SELECT segment_index, state FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "ORDER BY segment_index",
        (audiobook_id, chapter_index, locale),
    ).fetchall()

    completed = [r["segment_index"] for r in rows if r["state"] == "completed"]
    total = len(rows)
    return {
        "completed": completed,
        "total": total,
        "all_cached": len(completed) == total and total > 0,
    }


def _broadcast_segment_ready(
    audiobook_id: int, chapter_index: int, segment_index: int, locale: str
):
    """Push segment-ready event to all connected WebSocket clients."""
    connection_manager.broadcast(
        {
            "type": "segment_ready",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "segment_index": segment_index,
            "locale": locale,
        }
    )


def _broadcast_chapter_ready(audiobook_id: int, chapter_index: int, locale: str):
    """Push chapter-complete event to all connected WebSocket clients."""
    connection_manager.broadcast(
        {
            "type": "chapter_ready",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
        }
    )


def _broadcast_buffer_progress(
    audiobook_id: int, chapter_index: int, locale: str, completed: int, total: int
):
    """Push buffer progress update to connected clients."""
    connection_manager.broadcast(
        {
            "type": "buffer_progress",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
            "completed": completed,
            "total": total,
            "threshold": BUFFER_THRESHOLD,
        }
    )


# ── Routes ──


@streaming_bp.route("/api/translate/stream", methods=["POST"])
@guest_allowed
def request_streaming_translation():
    """Player requests on-demand translation for a book.

    Body:
        audiobook_id: int
        locale: str (e.g. "zh-Hans")
        chapter_index: int (default 0 — the chapter being played)

    Returns:
        - If all chapters are already cached: {state: "cached", chapters: [...]}
        - If streaming is needed: {state: "buffering", session_id: N, ...}
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    chapter_index = data.get("chapter_index", 0)

    if not audiobook_id:
        return jsonify({"error": "audiobook_id required"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()

    # Check if the active chapter already has subtitles + audio
    has_subs = _has_cached_subtitles(db, audiobook_id, chapter_index, locale)
    has_audio = _has_cached_audio(db, audiobook_id, chapter_index, locale)

    if has_subs and has_audio:
        # Check if ALL chapters are cached
        chapter_count = _get_chapter_count(db, audiobook_id)
        all_cached = True
        cached_chapters = []
        for ch in range(chapter_count):
            ch_has_subs = _has_cached_subtitles(db, audiobook_id, ch, locale)
            ch_has_audio = _has_cached_audio(db, audiobook_id, ch, locale)
            if ch_has_subs and ch_has_audio:
                cached_chapters.append(ch)
            else:
                all_cached = False

        return jsonify(
            {
                "state": "cached",
                "audiobook_id": audiobook_id,
                "chapter_index": chapter_index,
                "locale": locale,
                "cached_chapters": cached_chapters,
                "total_chapters": chapter_count,
                "all_cached": all_cached,
            }
        )

    # Need streaming — create or reuse a session
    existing = db.execute(
        "SELECT id, state FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (audiobook_id, locale),
    ).fetchone()

    if existing:
        session_id = existing["id"]
        # Update active chapter if it changed (user jumped chapters)
        db.execute(
            "UPDATE streaming_sessions SET active_chapter = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (chapter_index, session_id),
        )
        db.commit()
    else:
        cursor = db.execute(
            "INSERT INTO streaming_sessions (audiobook_id, locale, active_chapter, buffer_threshold) "
            "VALUES (?, ?, ?, ?)",
            (audiobook_id, locale, chapter_index, BUFFER_THRESHOLD),
        )
        session_id = cursor.lastrowid
        db.commit()

    # Ensure segment rows exist for the active chapter (priority 0 = active playback)
    _ensure_chapter_segments(db, audiobook_id, chapter_index, locale, priority=0)

    # Also pre-create segments for the next chapter (priority 1 = prefetch)
    chapter_count = _get_chapter_count(db, audiobook_id)
    if chapter_count and chapter_index + 1 < chapter_count:
        _ensure_chapter_segments(
            db, audiobook_id, chapter_index + 1, locale, priority=1
        )

    # Get bitmap for the active chapter
    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)

    return jsonify(
        {
            "state": "buffering",
            "session_id": session_id,
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
            "buffer_threshold": BUFFER_THRESHOLD,
            "segment_bitmap": bitmap,
        }
    )


@streaming_bp.route(
    "/api/translate/segments/<int:audiobook_id>/<int:chapter_index>/<locale>"
)
@guest_allowed
def get_segment_bitmap(audiobook_id, chapter_index, locale):
    """Get segment completion bitmap for a chapter.

    Used by the player to determine which segments are cached
    (instant seek) vs uncached (need buffering state).
    """
    try:
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid locale"}), 400

    db = _get_db()
    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)
    return jsonify(bitmap)


@streaming_bp.route("/api/translate/session/<int:audiobook_id>/<locale>")
@guest_allowed
def get_session_state(audiobook_id, locale):
    """Get current streaming session state."""
    try:
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid locale"}), 400

    db = _get_db()
    session = db.execute(
        "SELECT * FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? "
        "ORDER BY id DESC LIMIT 1",
        (audiobook_id, locale),
    ).fetchone()

    if not session:
        return jsonify({"state": "none"})

    return jsonify(
        {
            "session_id": session["id"],
            "state": session["state"],
            "active_chapter": session["active_chapter"],
            "buffer_threshold": session["buffer_threshold"],
            "gpu_warm": bool(session["gpu_warm"]),
        }
    )


@streaming_bp.route("/api/translate/warmup", methods=["POST"])
@guest_allowed
def warmup_gpu():
    """Pre-warm a GPU instance on app open.

    Called by the web UI on load to reduce cold-start latency
    when the user eventually presses play on an untranslated book.
    """
    # For now, this is a signal that a client connected.
    # The actual GPU warm-up will be handled by the translation daemon
    # when it sees this signal.
    logger.info("GPU warm-up requested by client")

    # Write a warm-up hint to DB so the daemon picks it up
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO streaming_sessions "
        "(audiobook_id, locale, state, gpu_warm) VALUES (0, 'warmup', 'warmup', 0)"
    )
    db.commit()

    return jsonify({"status": "warming"})


@streaming_bp.route("/api/translate/seek", methods=["POST"])
@guest_allowed
def handle_seek():
    """Handle a seek/skip into uncached territory.

    The player calls this when the user scrubs or skips beyond
    the cached segment range. The coordinator reprioritizes
    segment processing to start from the new position.

    Body:
        audiobook_id: int
        locale: str
        chapter_index: int
        segment_index: int (the segment at the seek target)
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    chapter_index = data.get("chapter_index", 0)
    segment_index = data.get("segment_index", 0)

    if not audiobook_id:
        return jsonify({"error": "audiobook_id required"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        segment_index = int(segment_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()

    # Ensure segments exist for this chapter
    _ensure_chapter_segments(db, audiobook_id, chapter_index, locale, priority=0)

    # Check if the target segment is already cached
    cached = db.execute(
        "SELECT state FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND segment_index = ? AND locale = ?",
        (audiobook_id, chapter_index, segment_index, locale),
    ).fetchone()

    if cached and cached["state"] == "completed":
        return jsonify({"state": "cached", "segment_index": segment_index})

    # Reprioritize: segments at and after the seek target get priority 0
    db.execute(
        "UPDATE streaming_segments SET priority = 2 "
        "WHERE audiobook_id = ? AND locale = ? AND state = 'pending'",
        (audiobook_id, locale),
    )
    db.execute(
        "UPDATE streaming_segments SET priority = 0 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND segment_index >= ? AND segment_index < ? AND state = 'pending'",
        (
            audiobook_id,
            chapter_index,
            locale,
            segment_index,
            segment_index + BUFFER_THRESHOLD,
        ),
    )

    # Update session active chapter
    db.execute(
        "UPDATE streaming_sessions SET active_chapter = ?, state = 'buffering', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (chapter_index, audiobook_id, locale),
    )
    db.commit()

    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)

    return jsonify(
        {
            "state": "buffering",
            "chapter_index": chapter_index,
            "segment_index": segment_index,
            "segment_bitmap": bitmap,
            "buffer_threshold": BUFFER_THRESHOLD,
        }
    )


# ── Worker callback endpoints (called by GPU workers) ──


@streaming_bp.route("/api/translate/segment-complete", methods=["POST"])
def segment_complete():
    """GPU worker reports a segment is done.

    Body:
        audiobook_id: int
        chapter_index: int
        segment_index: int
        locale: str
        vtt_content: str (optional — inline VTT cues)
        audio_path: str (optional — path to TTS audio segment)
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    chapter_index = data.get("chapter_index")
    segment_index = data.get("segment_index")
    locale = data.get("locale")

    if not all(
        [audiobook_id, locale, chapter_index is not None, segment_index is not None]
    ):
        return jsonify({"error": "missing fields"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        segment_index = int(segment_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()
    db.execute(
        "UPDATE streaming_segments SET state = 'completed', "
        "vtt_content = ?, audio_path = ?, completed_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND chapter_index = ? AND segment_index = ? AND locale = ?",
        (
            data.get("vtt_content"),
            data.get("audio_path"),
            audiobook_id,
            chapter_index,
            segment_index,
            locale,
        ),
    )
    db.commit()

    # Broadcast to WebSocket clients
    _broadcast_segment_ready(audiobook_id, chapter_index, segment_index, locale)

    # Check buffer progress for the active chapter
    bitmap = _get_segment_bitmap(db, audiobook_id, chapter_index, locale)
    completed_count = (
        len(bitmap["completed"]) if isinstance(bitmap["completed"], list) else 0
    )
    _broadcast_buffer_progress(
        audiobook_id,
        chapter_index,
        locale,
        completed_count,
        bitmap["total"],
    )

    # If this chapter is fully done, broadcast chapter_ready
    if bitmap["all_cached"]:
        _broadcast_chapter_ready(audiobook_id, chapter_index, locale)

        # Also write consolidated VTT to chapter_subtitles for permanent cache
        _consolidate_chapter(db, audiobook_id, chapter_index, locale)

    return jsonify({"status": "ok"})


@streaming_bp.route("/api/translate/chapter-complete", methods=["POST"])
def chapter_complete():
    """GPU worker reports an entire chapter is done (prefetch chapters).

    For prefetch chapters, the worker sends the complete VTT directly
    rather than segment-by-segment.

    Body:
        audiobook_id: int
        chapter_index: int
        locale: str
        source_vtt_path: str
        translated_vtt_path: str (optional)
        audio_path: str (optional)
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    chapter_index = data.get("chapter_index")
    locale = data.get("locale")

    if not all([audiobook_id, chapter_index is not None, locale]):
        return jsonify({"error": "missing fields"}), 400

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()

    # Insert into chapter_subtitles (permanent cache)
    if data.get("translated_vtt_path"):
        db.execute(
            "INSERT OR REPLACE INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider, translation_provider) "
            "VALUES (?, ?, ?, ?, 'streaming', 'deepl')",
            (audiobook_id, chapter_index, locale, data["translated_vtt_path"]),
        )
    if data.get("source_vtt_path"):
        db.execute(
            "INSERT OR REPLACE INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (?, ?, 'en', ?, 'streaming')",
            (audiobook_id, chapter_index, data["source_vtt_path"]),
        )

    # Insert into chapter_translations_audio if audio was generated
    if data.get("audio_path"):
        db.execute(
            "INSERT OR REPLACE INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (?, ?, ?, ?, 'streaming')",
            (audiobook_id, chapter_index, locale, data["audio_path"]),
        )

    db.commit()

    # Broadcast
    _broadcast_chapter_ready(audiobook_id, chapter_index, locale)

    return jsonify({"status": "ok"})


def _consolidate_chapter(db, audiobook_id: int, chapter_index: int, locale: str):
    """Merge streaming segments into a permanent chapter_subtitles entry.

    After all segments for a chapter are done, consolidate the VTT
    content into a single file and write to the permanent cache so
    future plays don't need the streaming pipeline.
    """
    rows = db.execute(
        "SELECT segment_index, vtt_content FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? AND state = 'completed' "
        "ORDER BY segment_index",
        (audiobook_id, chapter_index, locale),
    ).fetchall()

    if not rows:
        return

    # Merge VTT content from all segments
    all_vtt = "WEBVTT\n\n"
    for row in rows:
        if row["vtt_content"]:
            # Strip WEBVTT header from individual segments
            content = row["vtt_content"]
            if content.startswith("WEBVTT"):
                content = content.split("\n\n", 1)[-1] if "\n\n" in content else ""
            if content.strip():
                all_vtt += content.strip() + "\n\n"

    if len(all_vtt.strip()) <= len("WEBVTT"):
        return

    # Write consolidated VTT file — validated to live inside library root
    if _library_path is None:
        logger.error(
            "Cannot consolidate streaming chapter — library path not configured"
        )
        return
    try:
        vtt_path = _safe_subtitles_path(
            _library_path, audiobook_id, chapter_index, locale
        )
    except ValueError as exc:
        logger.error("Rejected unsafe consolidated VTT path: %s", _safe_log_value(exc))
        return
    vtt_path.parent.mkdir(parents=True, exist_ok=True)
    vtt_path.write_text(all_vtt)

    # Insert into permanent cache
    db.execute(
        "INSERT OR REPLACE INTO chapter_subtitles "
        "(audiobook_id, chapter_index, locale, vtt_path, stt_provider, translation_provider) "
        "VALUES (?, ?, ?, ?, 'streaming', 'deepl')",
        (audiobook_id, chapter_index, locale, str(vtt_path)),
    )
    db.commit()

    logger.info(
        "Consolidated streaming segments into permanent VTT: book=%d ch=%d locale=%s",
        audiobook_id,
        chapter_index,
        _safe_log_value(locale),
    )


def init_streaming_routes(database_path, library_path=None):
    """Initialize the streaming translation blueprint."""
    global _db_path, _library_path
    _db_path = Path(database_path) if database_path else None
    if library_path:
        _library_path = Path(library_path)
    else:
        # Default to the parent of the DB path
        _library_path = Path(database_path).parent if database_path else None


@streaming_bp.teardown_app_request
def _teardown_streaming_db(exc=None):
    _close_db(exc)
