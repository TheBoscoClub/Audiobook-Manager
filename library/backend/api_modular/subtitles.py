"""
Subtitle API blueprint.

Manages VTT subtitle files for audiobook chapters.

Endpoints:
    GET  /api/audiobooks/<id>/subtitles              — list subtitles for a book
    GET  /api/audiobooks/<id>/subtitles/<idx>/<locale> — get VTT path for a chapter+locale
    POST /api/subtitles/generate                      — generate subtitles for chapters (admin)

Generation runs chapter-by-chapter: the audio is split into chapters
via embedded metadata, each chapter is transcribed individually on the
GPU, and progress is reported between chapters so the frontend can show
"Chapter 3 of 42" style updates.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_file

from .auth import admin_or_localhost, guest_allowed

subtitles_bp = Blueprint("subtitles", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None

# ── Job status registry ──
# Keyed by (book_id, locale). Used by the frontend to poll a running STT job
# and show a helpful "waiting for GPU to spin up…" banner instead of a
# silent loading spinner. Also used to rate-limit duplicate user requests.
_job_status: dict[tuple[int, str], dict] = {}
_job_lock = threading.Lock()

# Per-user, per-book cooldown: a user can only request generation for the
# same book once every N seconds. Prevents accidental double-clicks from
# spawning two GPU jobs.
_USER_COOLDOWN_SEC = 30
_user_requests: dict[tuple[int, int], float] = {}


def _set_status(book_id: int, locale: str, **fields) -> None:
    key = (book_id, locale)
    with _job_lock:
        cur = _job_status.get(key, {})
        cur.update(fields)
        cur["updated_at"] = time.time()
        _job_status[key] = cur


def _get_status(book_id: int, locale: str) -> dict | None:
    with _job_lock:
        return dict(_job_status.get((book_id, locale), {})) or None


def _start_generation(
    book_id: int,
    locale: str,
    audio_path: Path,
    provider_name: str,
    skip_chapters: set[int] | None = None,
) -> None:
    """Launch subtitle generation in a background thread.

    Shared by both the admin and user-facing endpoints. Splits the
    audiobook into chapters and transcribes each individually, reporting
    progress between chapters.
    """
    db_path = str(_db_path)

    def _generate():
        try:
            _set_status(
                book_id,
                locale,
                state="starting",
                phase="loading_pipeline",
                message="Loading speech-to-text pipeline…",
                started_at=time.time(),
                provider=provider_name or "auto",
            )
            from localization.pipeline import generate_book_subtitles, get_stt_provider
            from localization.selection import WorkloadHint

            subtitle_dir = audio_path.parent / "subtitles"
            subtitle_dir.mkdir(parents=True, exist_ok=True)

            _set_status(
                book_id,
                locale,
                state="running",
                phase="gpu_spinup",
                message="Connecting to GPU transcription service…",
            )
            stt = get_stt_provider(provider_name, workload=WorkloadHint.LONG_FORM)
            _set_status(
                book_id,
                locale,
                phase="transcribing",
                message=f"Starting transcription with {stt.name}…",
                stt_provider=stt.name,
            )

            def _on_chapter_progress(ch_idx: int, total: int, title: str):
                _set_status(
                    book_id,
                    locale,
                    phase="transcribing",
                    message=f"Transcribing chapter {ch_idx + 1} of {total}: {title}",
                    chapter_index=ch_idx,
                    chapter_total=total,
                    chapter_title=title,
                )

            gen_conn = sqlite3.connect(db_path)
            gen_conn.execute("PRAGMA journal_mode=WAL")
            gen_conn.execute("PRAGMA foreign_keys=ON")
            try:

                def _on_chapter_complete(
                    ch_idx: int, source_vtt: Path, translated_vtt: Path | None
                ):
                    gen_conn.execute(
                        "INSERT OR REPLACE INTO chapter_subtitles "
                        "(audiobook_id, chapter_index, locale, vtt_path, "
                        " stt_provider, translation_provider) "
                        "VALUES (?, ?, 'en', ?, ?, NULL)",
                        (book_id, ch_idx, str(source_vtt), stt.name),
                    )
                    if translated_vtt:
                        gen_conn.execute(
                            "INSERT OR REPLACE INTO chapter_subtitles "
                            "(audiobook_id, chapter_index, locale, vtt_path, "
                            " stt_provider, translation_provider) "
                            "VALUES (?, ?, ?, ?, ?, 'deepl')",
                            (book_id, ch_idx, locale, str(translated_vtt), stt.name),
                        )
                    gen_conn.commit()
                    logger.info("Chapter %d subtitles saved — player can display them now", ch_idx)

                chapter_results = generate_book_subtitles(
                    audio_path=audio_path,
                    output_dir=subtitle_dir,
                    target_locale=locale,
                    stt_provider=stt,
                    on_progress=_on_chapter_progress,
                    on_chapter_complete=_on_chapter_complete,
                    skip_chapters=skip_chapters,
                )
            finally:
                gen_conn.close()

            logger.info("Subtitles saved for book %d: %d chapters", book_id, len(chapter_results))

            _set_status(
                book_id,
                locale,
                state="completed",
                phase="done",
                message=f"Subtitles ready — {len(chapter_results)} chapters.",
                finished_at=time.time(),
            )
        except Exception as e:
            logger.exception("Subtitle generation failed for book %d", book_id)
            _set_status(
                book_id,
                locale,
                state="failed",
                phase="error",
                message=(
                    "Subtitle generation failed. The GPU server may be "
                    "offline — please try again in a few minutes."
                ),
                error=str(e),
                finished_at=time.time(),
            )

    _set_status(
        book_id, locale, state="queued", phase="queued", message="Queued…", started_at=time.time()
    )
    threading.Thread(target=_generate, daemon=True).start()


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


def _streaming_subtitle_index(conn, book_id: int, locale_filter: str | None) -> list[dict]:
    """
    List (chapter_index, locale) pairs for which streaming_segments holds
    completed VTT content. "en" is synthesized from source_vtt_content on
    rows keyed by any target locale (English transcript is locale-agnostic);
    other locales come from vtt_content where streaming_segments.locale matches.

    Existence test only — one completed segment with non-NULL VTT is enough
    to advertise the chapter so subtitles.js starts polling it. Stitching
    happens in _stitch_streaming_vtt when the chapter VTT is fetched.
    """
    entries: list[dict] = []

    if locale_filter is None or locale_filter == "en":
        rows = conn.execute(
            "SELECT DISTINCT chapter_index FROM streaming_segments "
            "WHERE audiobook_id = ? AND state = 'completed' "
            "AND source_vtt_content IS NOT NULL AND length(source_vtt_content) > 0 "
            "ORDER BY chapter_index",
            (book_id,),
        ).fetchall()
        for r in rows:
            entries.append({"chapter_index": r["chapter_index"], "locale": "en"})

    if locale_filter is None:
        rows = conn.execute(
            "SELECT DISTINCT chapter_index, locale FROM streaming_segments "
            "WHERE audiobook_id = ? AND state = 'completed' "
            "AND vtt_content IS NOT NULL AND length(vtt_content) > 0 "
            "ORDER BY chapter_index, locale",
            (book_id,),
        ).fetchall()
        for r in rows:
            entries.append({"chapter_index": r["chapter_index"], "locale": r["locale"]})
    elif locale_filter != "en":
        rows = conn.execute(
            "SELECT DISTINCT chapter_index FROM streaming_segments "
            "WHERE audiobook_id = ? AND locale = ? AND state = 'completed' "
            "AND vtt_content IS NOT NULL AND length(vtt_content) > 0 "
            "ORDER BY chapter_index",
            (book_id, locale_filter),
        ).fetchall()
        for r in rows:
            entries.append({"chapter_index": r["chapter_index"], "locale": locale_filter})

    return entries


def _stitch_streaming_vtt(conn, book_id: int, chapter_index: int, locale: str) -> str | None:
    """
    Stitch per-segment VTT from streaming_segments into a single chapter VTT.

    Each segment row carries its own cue block with absolute chapter-relative
    timestamps (worker emits them that way), so we just emit one WEBVTT
    header and concat cue bodies in segment_index order. Returns None when no
    completed segments with VTT content exist for the chapter+locale.

    For locale='en', we pull source_vtt_content from any target locale's
    segments (DISTINCT by segment_index) — Whisper output is locale-agnostic.
    For other locales, we pull vtt_content from rows where locale matches.
    """
    if locale == "en":
        rows = conn.execute(
            "SELECT segment_index, source_vtt_content AS vtt "
            "FROM streaming_segments "
            "WHERE audiobook_id = ? AND chapter_index = ? "
            "AND state = 'completed' "
            "AND source_vtt_content IS NOT NULL AND length(source_vtt_content) > 0 "
            "GROUP BY segment_index "
            "ORDER BY segment_index",
            (book_id, chapter_index),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT segment_index, vtt_content AS vtt "
            "FROM streaming_segments "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ? "
            "AND state = 'completed' "
            "AND vtt_content IS NOT NULL AND length(vtt_content) > 0 "
            "ORDER BY segment_index",
            (book_id, chapter_index, locale),
        ).fetchall()

    if not rows:
        return None

    bodies: list[str] = []
    for r in rows:
        body = (r["vtt"] or "").strip()
        if not body:
            continue
        if body.upper().startswith("WEBVTT"):
            lines = body.split("\n", 1)
            body = lines[1].lstrip("\n") if len(lines) > 1 else ""
        if body:
            bodies.append(body)

    if not bodies:
        return None

    return "WEBVTT\n\n" + "\n\n".join(bodies) + "\n"


@subtitles_bp.route("/api/audiobooks/<int:book_id>/subtitles", methods=["GET"])
@guest_allowed
def get_book_subtitles(book_id):
    """List all subtitle entries for a book (cached + streaming)."""
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

        cached = [dict(r) for r in rows]
        seen = {(e["chapter_index"], e["locale"]) for e in cached}

        for s in _streaming_subtitle_index(conn, book_id, locale):
            key = (s["chapter_index"], s["locale"])
            if key not in seen:
                cached.append(s)
                seen.add(key)

        cached.sort(key=lambda e: (e["chapter_index"], e["locale"]))
        return jsonify(cached)
    finally:
        conn.close()


@subtitles_bp.route(
    "/api/audiobooks/<int:book_id>/subtitles/<int:chapter_index>/<locale>", methods=["GET"]
)
@guest_allowed
def get_chapter_subtitle(book_id, chapter_index, locale):
    """Get or serve the VTT for a specific chapter and locale.

    Cached chapter_subtitles row wins; otherwise stitch partial VTT from
    streaming_segments so in-flight streaming sessions expose live subtitles
    as segments complete (subtitles.js polls this endpoint every 5 s).
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
            (book_id, chapter_index, locale),
        ).fetchone()

        cached_file_missing = False
        if row:
            vtt_path = Path(row["vtt_path"])
            if not vtt_path.is_absolute() and _library_path:
                vtt_path = _library_path / vtt_path
            if vtt_path.exists():
                return send_file(vtt_path, mimetype="text/vtt; charset=utf-8", as_attachment=False)
            cached_file_missing = True

        stitched = _stitch_streaming_vtt(conn, book_id, chapter_index, locale)
        if stitched:
            return (
                stitched,
                200,
                {"Content-Type": "text/vtt; charset=utf-8"},
            )

        if cached_file_missing:
            return jsonify({"error": "VTT file missing on disk"}), 404
        return jsonify({"error": "Subtitle not found"}), 404
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
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        audio_path = Path(book["file_path"])
        if not audio_path.exists():
            return jsonify({"error": "Audio file not found on disk"}), 404

        existing_chapters = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT chapter_index FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = ?",
                (book_id, "en"),
            ).fetchall()
        }
    finally:
        conn.close()

    _start_generation(book_id, locale, audio_path, provider_name, skip_chapters=existing_chapters)

    return jsonify(
        {
            "audiobook_id": book_id,
            "locale": locale,
            "status": "started",
            "message": "Subtitle generation started in background.",
        }
    )


@subtitles_bp.route("/api/subtitles/status/<int:book_id>/<locale>", methods=["GET"])
@guest_allowed
def get_subtitle_job_status(book_id, locale):
    """Poll the progress of a running subtitle-generation job.

    Returns a JSON document the frontend uses to render a friendly
    "spinning up GPU" / "transcribing" / "done" banner. Safe for
    unauthenticated guests — reveals nothing beyond a phase label.
    """
    status = _get_status(book_id, locale)
    if not status:
        return jsonify({"state": "idle"})
    return jsonify({"audiobook_id": book_id, "locale": locale, **status})


def _extract_user_id(user):
    """Return user's id from dict or attribute form, or None."""
    if user is None:
        return None
    return user.get("id") if isinstance(user, dict) else getattr(user, "id", None)


def _check_user_cooldown(user_id, book_id):
    """Return a (response, status_code) tuple if cooldown applies, else None."""
    if user_id is None:
        return None
    key = (int(user_id), int(book_id))
    now = time.time()
    last = _user_requests.get(key, 0)
    if now - last < _USER_COOLDOWN_SEC:
        return (
            jsonify(
                {
                    "status": "cooldown",
                    "message": "Please wait a moment before trying again.",
                    "retry_after": int(_USER_COOLDOWN_SEC - (now - last)),
                }
            ),
            429,
        )
    _user_requests[key] = now
    return None


def _load_book_for_subtitle_request(book_id):
    """Return (audio_path, existing_chapters) or a (response, status_code) error tuple."""
    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, file_path FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return (jsonify({"error": "Audiobook not found"}), 404)
        audio_path = Path(book["file_path"])
        if not audio_path.exists():
            return (jsonify({"error": "Audio file not found on disk"}), 404)
        existing_chapters = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT chapter_index FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = ?",
                (book_id, "en"),
            ).fetchall()
        }
    finally:
        conn.close()
    return (audio_path, existing_chapters)


@subtitles_bp.route("/api/user/subtitles/request", methods=["POST"])
@guest_allowed
def user_request_subtitles():
    """Allow a signed-in user to request subtitle generation for a book.

    Rate-limited per (user, book) via a short cooldown so accidental
    double-clicks don't spawn duplicate GPU jobs.
    """
    from flask import current_app

    auth_on = current_app.config.get("AUTH_ENABLED", False)
    user = getattr(g, "user", None)
    if auth_on and not user:
        return jsonify({"error": "Sign in to request subtitles"}), 401

    data = request.get_json(silent=True) or {}
    book_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    if not book_id:
        return jsonify({"error": "audiobook_id is required"}), 400

    user_id = _extract_user_id(user)
    cooldown_resp = _check_user_cooldown(user_id, book_id)
    if cooldown_resp is not None:
        return cooldown_resp

    # If a job for this (book, locale) is already running, return its state
    existing = _get_status(int(book_id), locale)
    if existing and existing.get("state") in ("queued", "starting", "running"):
        return jsonify(
            {"audiobook_id": book_id, "locale": locale, "status": "already_running", **existing}
        )

    loaded = _load_book_for_subtitle_request(book_id)
    # If loader returned an error response (2-tuple with int status), pass through.
    if isinstance(loaded[1], int):
        return loaded
    audio_path, existing_chapters = loaded

    _start_generation(int(book_id), locale, audio_path, "", skip_chapters=existing_chapters)

    return jsonify(
        {
            "audiobook_id": book_id,
            "locale": locale,
            "status": "started",
            "message": (
                "Subtitle generation started. This may take several minutes — "
                "the GPU server has to spin up first."
            ),
        }
    )
