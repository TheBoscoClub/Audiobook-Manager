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
    POST /api/translate/stop             — stop streaming (demote all pending to back-fill)
"""

import logging
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, request, send_file

from i18n import SUPPORTED_LOCALES

from .auth import guest_allowed
from .websocket import connection_manager

streaming_bp = Blueprint("streaming_translate", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None
# Root directory where per-segment opus files are stored — set by
# `init_streaming_routes`. Task 10 concatenates per-segment files from here
# into chapter-level opus consolidation output.
_streaming_audio_root: Path | None = None

# Per-locale default edge-tts voice. MUST be kept in sync with the worker's
# `_LOCALE_DEFAULT_VOICE` mapping in scripts/stream-translate-worker.py — the
# worker selects the voice at synthesis time, but the server records it on the
# consolidated chapter row. Inlined rather than imported because the worker
# lives at a hyphenated script path that is not a valid Python module name.
_LOCALE_DEFAULT_VOICE = {
    "zh-Hans": "zh-CN-XiaoxiaoNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "zh-Hant": "zh-TW-HsiaoChenNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
}


def _default_voice_for_locale(locale: str) -> str:
    """Map locale → edge-tts voice. Unknown → en-US fallback.

    Must match the worker's `_default_voice_for_locale` semantics.
    """
    return _LOCALE_DEFAULT_VOICE.get(locale, "en-US-AriaNeural")


def _probe_audio_duration(audio_path: Path) -> float | None:
    """Return the duration of an audio file in seconds, or None on error."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None

SEGMENT_DURATION_SEC = 30
# Cursor buffer window: the number of segments at and ahead of the playback
# cursor that get P0 (highest) priority. 6 × 30s = 3 minutes.
BUFFER_AHEAD_SEGMENTS = 6
# Alias preserved for callers that reference the session-level "buffer_threshold"
# knob (web JS, schema default, broadcast payloads). The two values must stay
# equal — the web UI thresholds match the cursor-buffer semantic.
BUFFER_THRESHOLD = BUFFER_AHEAD_SEGMENTS

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


def _close_db(exc=None):  # pylint: disable=unused-argument  # required by Flask teardown_appcontext signature
    db = getattr(g, "_streaming_db", None)
    if db is not None:
        db.close()


def _has_cached_subtitles(db, audiobook_id: int, chapter_index: int, locale: str) -> bool:
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
        "SELECT MAX(chapter_index) + 1 as cnt FROM chapter_subtitles WHERE audiobook_id = ?",
        (audiobook_id,),
    ).fetchone()
    if row and row["cnt"]:
        return row["cnt"]
    # Fallback: check translation queue for chapter count
    row = db.execute(
        "SELECT total_chapters FROM translation_queue WHERE audiobook_id = ?", (audiobook_id,)
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


def _get_chapter_duration_sec(db, audiobook_id: int, chapter_index: int) -> float:  # pylint: disable=unused-argument  # chapter_index reserved for future per-chapter duration lookup; current impl returns average
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


def _derive_phase(conn, audiobook_id: int, locale: str) -> str:
    """Derive the current streaming-pipeline phase for (audiobook_id, locale).

    The phase is surfaced to the player via both the REST
    ``POST /api/translate/stream`` response and the WebSocket
    ``buffer_progress`` broadcast so the UI can render a distinct label
    for each pipeline stage (e.g. Qing's monolingual zh-Hans player).

    Precedence (first match wins):
        1. failed > 0                                 → "error"
        2. completed >= BUFFER_AHEAD_SEGMENTS         → "streaming"
        3. processing > 0                             → "buffering"
        4. session warm + pending > 0                 → "gpu_provisioning"
        5. session warm + pending=0 + processing=0    → "warmup"
        6. no warm session + pending > 0              → "warmup"
        7. otherwise                                  → "idle"

    Schema-drift note vs the v8.3.2 plan text: the plan referenced a
    ``requested_at`` column (real column is ``created_at``) and a session
    state ``'warmup'`` (no row ever writes that; warmup is modelled via
    ``gpu_warm=1``). This helper follows the real schema.
    """
    counts_row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN state = 'processing' THEN 1 ELSE 0 END) AS processing, "
        "SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END) AS completed, "
        "SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed "
        "FROM streaming_segments "
        "WHERE audiobook_id = ? AND locale = ?",
        (audiobook_id, locale),
    ).fetchone()

    # SUM over an empty set returns NULL → None in Python.
    pending = (counts_row["pending"] or 0) if counts_row else 0
    processing = (counts_row["processing"] or 0) if counts_row else 0
    completed = (counts_row["completed"] or 0) if counts_row else 0
    failed = (counts_row["failed"] or 0) if counts_row else 0

    session = conn.execute(
        "SELECT state, gpu_warm FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (audiobook_id, locale),
    ).fetchone()
    gpu_warm = bool(session["gpu_warm"]) if session is not None else False

    if failed > 0:
        return "error"
    if completed >= BUFFER_AHEAD_SEGMENTS:
        return "streaming"
    if processing > 0:
        return "buffering"
    if gpu_warm and pending > 0:
        return "gpu_provisioning"
    if gpu_warm and pending == 0 and processing == 0:
        return "warmup"
    if pending > 0:
        return "warmup"
    return "idle"


def _get_current_segment(
    conn, audiobook_id: int, chapter_index: int, locale: str
) -> int:
    """Return the next-to-play segment index for the active chapter.

    Defined as the lowest ``segment_index`` in state ``'processing'`` or
    ``'pending'`` for this (audiobook_id, chapter_index, locale). If no
    such row exists (all completed or none created yet), returns the count
    of completed segments — i.e. the next index to fill.
    """
    row = conn.execute(
        "SELECT MIN(segment_index) AS cur FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state IN ('processing', 'pending')",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    if row is not None and row["cur"] is not None:
        return int(row["cur"])

    completed_row = conn.execute(
        "SELECT COUNT(*) AS n FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state = 'completed'",
        (audiobook_id, chapter_index, locale),
    ).fetchone()
    return int(completed_row["n"]) if completed_row else 0


def _broadcast_buffer_progress(
    audiobook_id: int,
    chapter_index: int,
    locale: str,
    completed: int,
    total: int,
    phase: str,
):
    """Push buffer progress update to connected clients.

    ``phase`` is computed by the caller (the caller already holds the DB
    connection needed by :func:`_derive_phase`) and is forwarded verbatim
    to the WebSocket payload so the player can render the stage label in
    the same tick as the progress update.
    """
    connection_manager.broadcast(
        {
            "type": "buffer_progress",
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
            "completed": completed,
            "total": total,
            "threshold": BUFFER_THRESHOLD,
            "phase": phase,
        }
    )


# ── Pure reprioritization impls (cursor-centric 3-tier queue) ──
#
# The priority model:
#   P0 (0) — cursor buffer: the 6 segments at and just ahead of the seek target
#   P1 (1) — forward chase: remaining pending segments in the same chapter
#            with segment_index > t + BUFFER_AHEAD_SEGMENTS - 1
#   P2 (2) — back-fill: pending segments in the same chapter with
#            segment_index < t (plus everything on stop())
#
# Scope is chapter-local: seek only reshuffles within (audiobook_id, chapter,
# locale). Processing rows are never touched — the worker has claimed them.


def handle_seek_impl(conn, audiobook_id, locale, chapter_index, segment_index):
    """Reprioritize pending segments around a new cursor position.

    Writes three UPDATEs (all scoped to state='pending') and commits:
      1. Demote everything pending in this chapter to P2.
      2. Promote the cursor window [t .. t+BUFFER_AHEAD_SEGMENTS-1] to P0.
      3. Promote forward-chase (> t+BUFFER_AHEAD_SEGMENTS-1) to P1.

    Segments in state='processing' or 'completed' are never touched.
    """
    end = segment_index + BUFFER_AHEAD_SEGMENTS
    # 1. Demote all pending segments in this chapter → P2
    conn.execute(
        "UPDATE streaming_segments SET priority = 2 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state = 'pending'",
        (audiobook_id, chapter_index, locale),
    )
    # 2. Promote cursor window [t, t+BUFFER_AHEAD_SEGMENTS) → P0
    conn.execute(
        "UPDATE streaming_segments SET priority = 0 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND segment_index >= ? AND segment_index < ? AND state = 'pending'",
        (audiobook_id, chapter_index, locale, segment_index, end),
    )
    # 3. Promote forward-chase (beyond the cursor window) → P1
    conn.execute(
        "UPDATE streaming_segments SET priority = 1 "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND segment_index >= ? AND state = 'pending'",
        (audiobook_id, chapter_index, locale, end),
    )
    conn.commit()


def stop_streaming_impl(conn, audiobook_id, locale):
    """Demote every pending segment for (book, locale) to P2 (back-fill).

    Used when the player stops streaming translation. Processing segments
    are not touched — let the worker finish what it claimed. No promotion.
    """
    conn.execute(
        "UPDATE streaming_segments SET priority = 2 "
        "WHERE audiobook_id = ? AND locale = ? AND state = 'pending'",
        (audiobook_id, locale),
    )
    conn.commit()


# ── Routes ──


def _parse_stream_request(data):
    """Extract+validate fields from /api/translate/stream payload.

    Returns (audiobook_id, locale, chapter_index, err_response_or_None).
    """
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    chapter_index = data.get("chapter_index", 0)

    if not audiobook_id:
        return None, None, None, (jsonify({"error": "audiobook_id required"}), 400)

    try:
        audiobook_id = int(audiobook_id)
        chapter_index = int(chapter_index)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return None, None, None, (jsonify({"error": "invalid parameters"}), 400)

    return audiobook_id, locale, chapter_index, None


def _fully_cached_response(db, audiobook_id, chapter_index, locale):
    """Build the response when the active chapter is cached. Enumerates
    all chapters to report which others are cached.
    """
    chapter_count = _get_chapter_count(db, audiobook_id)
    all_cached = True
    cached_chapters = []
    for ch in range(chapter_count):
        if _has_cached_subtitles(db, audiobook_id, ch, locale) and _has_cached_audio(
            db, audiobook_id, ch, locale
        ):
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
            # Fully-cached chapters are effectively already streaming — the
            # player can immediately play from the permanent cache.
            "phase": "streaming",
        }
    )


def _get_or_create_streaming_session(db, audiobook_id, locale, chapter_index):
    """Return session_id, creating a streaming_sessions row or updating
    an existing buffering/streaming session's active_chapter.
    """
    existing = db.execute(
        "SELECT id, state FROM streaming_sessions "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (audiobook_id, locale),
    ).fetchone()

    if existing:
        session_id = existing["id"]
        db.execute(
            "UPDATE streaming_sessions SET active_chapter = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (chapter_index, session_id),
        )
    else:
        cursor = db.execute(
            "INSERT INTO streaming_sessions "
            "(audiobook_id, locale, active_chapter, buffer_threshold) "
            "VALUES (?, ?, ?, ?)",
            (audiobook_id, locale, chapter_index, BUFFER_THRESHOLD),
        )
        session_id = cursor.lastrowid
    db.commit()
    return session_id


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
    audiobook_id, locale, chapter_index, err = _parse_stream_request(
        request.get_json(silent=True) or {}
    )
    if err:
        return err

    db = _get_db()

    if _has_cached_subtitles(db, audiobook_id, chapter_index, locale) and _has_cached_audio(
        db, audiobook_id, chapter_index, locale
    ):
        return _fully_cached_response(db, audiobook_id, chapter_index, locale)

    session_id = _get_or_create_streaming_session(db, audiobook_id, locale, chapter_index)

    # Ensure segment rows exist for the active chapter (priority 0 = active playback)
    _ensure_chapter_segments(db, audiobook_id, chapter_index, locale, priority=0)

    # Also pre-create segments for the next chapter (priority 1 = prefetch)
    chapter_count = _get_chapter_count(db, audiobook_id)
    if chapter_count and chapter_index + 1 < chapter_count:
        _ensure_chapter_segments(db, audiobook_id, chapter_index + 1, locale, priority=1)

    return jsonify(
        {
            "state": "buffering",
            "session_id": session_id,
            "audiobook_id": audiobook_id,
            "chapter_index": chapter_index,
            "locale": locale,
            "buffer_threshold": BUFFER_THRESHOLD,
            "segment_bitmap": _get_segment_bitmap(db, audiobook_id, chapter_index, locale),
            "phase": _derive_phase(db, audiobook_id, locale),
            "current_segment": _get_current_segment(
                db, audiobook_id, chapter_index, locale
            ),
        }
    )


@streaming_bp.route("/api/translate/segments/<int:audiobook_id>/<int:chapter_index>/<locale>")
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

    # 3-tier cursor-centric reprioritization (scoped to this chapter).
    handle_seek_impl(db, audiobook_id, locale, chapter_index, segment_index)

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


@streaming_bp.route("/api/translate/stop", methods=["POST"])
@guest_allowed
def stop_streaming():
    """Stop streaming translation for a book+locale.

    Demotes every pending segment for (audiobook_id, locale) to P2
    (back-fill priority) so the GPU workers stop chasing the cursor for
    this book. Processing segments are left alone — the worker finishes
    what it claimed. Any active streaming_sessions row for this pair is
    marked 'stopped'.

    Body:
        audiobook_id: int
        locale: str
    """
    data = request.get_json(silent=True) or {}
    audiobook_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")

    if not audiobook_id:
        return jsonify({"error": "audiobook_id required"}), 400

    try:
        audiobook_id = int(audiobook_id)
        locale = _sanitize_locale(locale)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid parameters"}), 400

    db = _get_db()

    stop_streaming_impl(db, audiobook_id, locale)

    db.execute(
        "UPDATE streaming_sessions SET state = 'stopped', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE audiobook_id = ? AND locale = ? AND state IN ('buffering', 'streaming')",
        (audiobook_id, locale),
    )
    db.commit()

    return jsonify(
        {
            "state": "stopped",
            "audiobook_id": audiobook_id,
            "locale": locale,
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

    if audiobook_id is None or locale is None or chapter_index is None or segment_index is None:
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
    completed_count = len(bitmap["completed"]) if isinstance(bitmap["completed"], list) else 0
    phase = _derive_phase(db, audiobook_id, locale)
    _broadcast_buffer_progress(
        audiobook_id, chapter_index, locale, completed_count, bitmap["total"], phase
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

    if audiobook_id is None or chapter_index is None or locale is None:
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


def _consolidate_chapter_audio(
    db,
    audiobook_id: int,
    chapter_index: int,
    locale: str,
) -> None:
    """Concatenate per-segment opus files into chapter.opus and persist a row.

    All completed segments must have `audio_path` set (Task 9's TTS may
    degrade to text-only on failure — those chapters produce no consolidated
    audio). On any error, logs and returns without raising; VTT
    consolidation continues unaffected in the caller.
    """
    if _streaming_audio_root is None:
        logger.warning(
            "Cannot consolidate chapter audio — _streaming_audio_root not configured"
        )
        return

    # Pull (segment_index, audio_path) for all completed segments to confirm
    # every one has audio before we attempt to concat.
    audio_rows = db.execute(
        "SELECT segment_index, audio_path FROM streaming_segments "
        "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
        "AND state = 'completed' "
        "ORDER BY segment_index",
        (audiobook_id, chapter_index, locale),
    ).fetchall()

    if not audio_rows:
        return

    if any(r["audio_path"] is None for r in audio_rows):
        logger.info(
            "Skipping chapter audio consolidation — at least one segment "
            "has no audio_path (TTS degraded to text-only): "
            "book=%d ch=%d locale=%s",
            audiobook_id,
            chapter_index,
            _safe_log_value(locale),
        )
        return

    # Resolve each per-segment relative path to an absolute path under the
    # streaming audio root, and verify the file exists on disk.
    segment_paths: list[Path] = []
    for r in audio_rows:
        rel = r["audio_path"]
        p = _streaming_audio_root / rel if not os.path.isabs(rel) else Path(rel)
        if not p.exists():
            logger.warning(
                "Missing per-segment opus on disk — skipping chapter audio "
                "consolidation: book=%d ch=%d seg=%d path=%s",
                audiobook_id,
                chapter_index,
                r["segment_index"],
                _safe_log_value(p),
            )
            return
        segment_paths.append(p)

    # Output: <root>/<book_id>/ch<NNN>/<locale>/chapter.opus
    chapter_dir = (
        _streaming_audio_root / str(audiobook_id) / f"ch{chapter_index:03d}" / locale
    )
    chapter_dir.mkdir(parents=True, exist_ok=True)
    out_path = chapter_dir / "chapter.opus"

    # ffmpeg concat demuxer with -c copy. All per-segment opus files are
    # uniform 48k/48kHz libopus (enforced by Task 9), so no re-encode is
    # needed — sub-second latency.
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            concat_list = Path(tmp_dir) / "concat.txt"
            concat_list.write_text(
                "\n".join(f"file '{p}'" for p in segment_paths) + "\n"
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(out_path),
                ],
                check=True,
                capture_output=True,
            )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning(
            "ffmpeg concat failed for chapter audio: book=%d ch=%d locale=%s err=%s",
            audiobook_id,
            chapter_index,
            _safe_log_value(locale),
            _safe_log_value(exc),
        )
        return

    duration = _probe_audio_duration(out_path)
    voice = _default_voice_for_locale(locale)

    try:
        db.execute(
            "INSERT OR REPLACE INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, "
            " tts_provider, tts_voice, duration_seconds) "
            "VALUES (?, ?, ?, ?, 'streaming', ?, ?)",
            (
                audiobook_id,
                chapter_index,
                locale,
                str(out_path),
                voice,
                duration,
            ),
        )
        db.commit()
    except sqlite3.DatabaseError as exc:
        logger.warning(
            "Failed to persist chapter audio row: book=%d ch=%d locale=%s err=%s",
            audiobook_id,
            chapter_index,
            _safe_log_value(locale),
            _safe_log_value(exc),
        )
        return

    logger.info(
        "Consolidated streaming segments into chapter.opus: "
        "book=%d ch=%d locale=%s segments=%d duration=%s",
        audiobook_id,
        chapter_index,
        _safe_log_value(locale),
        len(segment_paths),
        duration,
    )


def _consolidate_chapter(db, audiobook_id: int, chapter_index: int, locale: str):
    """Merge streaming segments into a permanent chapter_subtitles entry.

    After all segments for a chapter are done, consolidate the VTT
    content into a single file and write to the permanent cache so
    future plays don't need the streaming pipeline. If every segment
    also has a per-segment opus audio file (Task 9), concatenate them
    into a single chapter.opus and register a chapter_translations_audio
    row so `_has_cached_audio` returns True on next play.
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
        logger.error("Cannot consolidate streaming chapter — library path not configured")
        return
    try:
        vtt_path = _safe_subtitles_path(_library_path, audiobook_id, chapter_index, locale)
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

    # Audio consolidation is a best-effort addition — any failure logs and
    # leaves the VTT-side cache intact.
    try:
        _consolidate_chapter_audio(db, audiobook_id, chapter_index, locale)
    except Exception as exc:  # pylint: disable=broad-except  # defense in depth — audio side must never break VTT path
        logger.warning(
            "Chapter audio consolidation raised unexpected exception: "
            "book=%d ch=%d locale=%s err=%s",
            audiobook_id,
            chapter_index,
            _safe_log_value(locale),
            _safe_log_value(exc),
        )


@streaming_bp.route(
    "/streaming-audio/<int:audiobook_id>/<int:chapter_index>/<int:segment_index>/<locale>"
)
@guest_allowed
def serve_streaming_segment(audiobook_id, chapter_index, segment_index, locale):
    """Serve a per-segment opus file to the client MSE chain.

    Path layout (owned by the streaming worker):
        ``<_streaming_audio_root>/<book>/ch<NNN>/<locale>/seg<NNNN>.opus``

    Defense in depth:
    - Reject locales not in ``SUPPORTED_LOCALES`` (whitelist).
    - Resolve the requested path and the root, then verify containment.
      This catches ``..`` traversal, symlink escape, and any future
      race window where the segment directory is replaced.
    - Return 503 if the streaming root was never configured
      (``init_streaming_routes`` not yet called) — distinct from 404 so
      ops can tell "missing file" from "misconfigured deployment".
    """
    # Whitelist check. Routes that reached here with a bogus locale slug
    # (``xx``, ``..``) are rejected before any filesystem work happens.
    if locale not in SUPPORTED_LOCALES:
        abort(404)

    if _streaming_audio_root is None:
        # Route was hit before init_streaming_routes configured the root.
        abort(503)

    # Resolve BOTH sides of the containment check. The module global is
    # stored unresolved by init_streaming_routes, so we resolve here; if
    # the target is a symlink pointing outside, .resolve() on the
    # candidate exposes that and the containment check rejects it.
    try:
        root = _streaming_audio_root.resolve(strict=False)
        candidate = (
            _streaming_audio_root
            / str(audiobook_id)
            / f"ch{chapter_index:03d}"
            / locale
            / f"seg{segment_index:04d}.opus"
        )
        # strict=False so a missing file still resolves (we check exists()
        # below and return 404); strict=True would raise FileNotFoundError.
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        # Resolve can raise on broken symlink loops; treat as not-found.
        abort(404)

    # Containment: the resolved candidate must live under the resolved
    # root. Using .is_relative_to (3.9+); equivalent to "root in parents"
    # but correctly handles the edge case where resolved == root.
    if not resolved.is_relative_to(root):
        abort(403)

    if not resolved.is_file():
        abort(404)

    # conditional=True enables HTTP Range/If-Modified-Since handling,
    # which MSE SourceBuffer.appendBuffer relies on for resumable fetches.
    return send_file(
        resolved,
        mimetype="audio/ogg; codecs=opus",
        conditional=True,
    )


def init_streaming_routes(database_path, library_path=None, streaming_audio_dir=None):
    """Initialize the streaming translation blueprint.

    Args:
        database_path: Path to the main audiobooks SQLite database.
        library_path: Library root (for VTT subtitle writes); defaults
            to the DB file's parent directory.
        streaming_audio_dir: Root directory holding per-segment opus
            files (used by Task 10 chapter audio consolidation).
            Defaults to $AUDIOBOOKS_STREAMING_AUDIO_DIR or the canonical
            default /var/lib/audiobooks/streaming-audio path.
    """
    global _db_path, _library_path, _streaming_audio_root
    _db_path = Path(database_path) if database_path else None
    if library_path:
        _library_path = Path(library_path)
    else:
        # Default to the parent of the DB path
        _library_path = Path(database_path).parent if database_path else None
    if streaming_audio_dir:
        _streaming_audio_root = Path(streaming_audio_dir)
    else:
        # Derive from AUDIOBOOKS_VAR_DIR to match library/config.py's canonical
        # chain (library/config.py:143-150). Direct env reads are used here
        # because this module is imported early by the API factory, before
        # library.config loading completes.
        _var_dir = os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
        _streaming_audio_root = Path(
            os.environ.get(
                "AUDIOBOOKS_STREAMING_AUDIO_DIR",
                f"{_var_dir}/streaming-audio",
            )
        )


@streaming_bp.teardown_app_request
def _teardown_streaming_db(exc=None):
    _close_db(exc)
