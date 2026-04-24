"""
Translated Audio API blueprint.

Manages TTS-generated translated audio for audiobook chapters.

Endpoints:
    GET  /api/audiobooks/<id>/translated-audio              — list translated audio
    GET  /api/audiobooks/<id>/translated-audio/<idx>/<locale> — stream translated chapter
    POST /api/translated-audio/generate                      — generate translated audio (admin)
    GET  /api/translated-audio/status/<id>/<locale>          — poll TTS job progress
    POST /api/user/translated-audio/request                  — user-facing generation request
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_file

from .auth import admin_or_localhost, guest_allowed

translated_audio_bp = Blueprint("translated_audio", __name__)
logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None

# ── Job status registry ──
_job_status: dict[tuple[int, str], dict] = {}
_job_lock = threading.Lock()

_USER_COOLDOWN_SEC = 60
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


@translated_audio_bp.route("/api/audiobooks/<int:book_id>/translated-audio", methods=["GET"])
@guest_allowed
def get_book_translated_audio(book_id):
    """List all translated audio entries for a book.

    Hides ONLY sampler-produced rows (``audio_path`` under
    ``streaming-audio/``) when the sampler job for this (book, locale) is
    not yet complete. Legacy chapter-batch translations (files under the
    library's ``translated/`` subdir) are always returned — they were
    produced by the old per-chapter batch pipeline, are fully playable
    end-to-end, and do not depend on the sampler's progress.

    Rationale: a partial sample (e.g. 1/13 segments done) wrote its first
    chapter's consolidated ``.webm`` into ``chapter_translations_audio``,
    which the frontend treats as "full translation available" and plays —
    only to dead-end after 30s because the rest of the chapters are not
    sampled yet. When a book has BOTH legacy full rows AND a partial
    sampler row for the same chapter, the sampler row would also be
    hidden; the legacy rows for the remaining chapters play normally.
    When a book has ONLY sampler rows (pre-sampler translation never
    ran), suppressing them lets the frontend fall through to live
    on-demand streaming.
    """
    locale = request.args.get("locale")
    conn = _get_db()
    try:
        # Determine whether each row is sampler-produced by path prefix.
        # AUDIOBOOKS_STREAMING_AUDIO_DIR is the canonical sampler+stream
        # output location; legacy rows live under AUDIOBOOKS_LIBRARY.
        import os as _os

        sampler_audio_prefix = _os.environ.get(
            "AUDIOBOOKS_STREAMING_AUDIO_DIR",
            f"{_os.environ.get('AUDIOBOOKS_VAR_DIR', '/var/lib/audiobooks')}/streaming-audio",
        ).rstrip("/")

        def _is_sampler_row(row):
            return (row["audio_path"] or "").startswith(sampler_audio_prefix + "/")

        # Sampler-incomplete check — cached per (book, locale) within the call.
        _sampler_cache: dict = {}

        def _sampler_incomplete(loc):
            if loc in _sampler_cache:
                return _sampler_cache[loc]
            try:
                job = conn.execute(
                    "SELECT status FROM sampler_jobs "
                    "WHERE audiobook_id = ? AND locale = ? LIMIT 1",
                    (book_id, loc),
                ).fetchone()
                blocked = job is not None and job["status"] != "complete"
            except sqlite3.OperationalError:
                blocked = False  # older installs w/o sampler_jobs
            _sampler_cache[loc] = blocked
            return blocked

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

        # Filter out SAMPLER-ORIGINATED rows when sampler is incomplete.
        # Legacy batch-translation rows are always kept.
        filtered = [
            r for r in rows
            if not (_is_sampler_row(r) and _sampler_incomplete(r["locale"]))
        ]
        return jsonify([dict(r) for r in filtered])
    finally:
        conn.close()


@translated_audio_bp.route(
    "/api/audiobooks/<int:book_id>/translated-audio/<int:chapter_index>/<locale>", methods=["GET"]
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
        mime_map = {
            ".opus": "audio/opus",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
        }
        mimetype = mime_map.get(suffix, "audio/opus")
        return send_file(audio_path, mimetype=mimetype, as_attachment=False)
    finally:
        conn.close()


@translated_audio_bp.route("/api/translated-audio/generate", methods=["POST"])
@admin_or_localhost
def generate_translated_audio():
    """Generate translated audio for audiobook chapters via TTS.

    Requires translated subtitles to exist first (reads VTT for text).
    Provider defaults to AUDIOBOOKS_TTS_PROVIDER (edge-tts unless overridden
    in audiobooks.conf). Admins can override per-request with `provider`.

    Request body:
        {
            "audiobook_id": 42,
            "locale": "zh-Hans",
            "voice": "zh-CN-XiaoxiaoNeural",
            "provider": "xtts-vastai"   # optional — overrides config
        }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    book_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    voice = data.get("voice", "zh-CN-XiaoxiaoNeural")
    provider_override = data.get("provider")

    if not book_id:
        return jsonify({"error": "audiobook_id is required"}), 400

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?", (book_id,)
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
            return (
                jsonify(
                    {
                        "error": "Translated subtitles not found. "
                        "Generate subtitles first via POST /api/subtitles/generate"
                    }
                ),
                400,
            )

        # Check if translated audio already exists
        existing = conn.execute(
            "SELECT id FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if existing:
            return jsonify(
                {
                    "audiobook_id": book_id,
                    "status": "exists",
                    "message": "Translated audio already exists for this book.",
                }
            )

        vtt_path = Path(sub_row["vtt_path"])
        audio_file_path = Path(book["file_path"])
    finally:
        conn.close()

    db_path = str(_db_path)

    def _generate():
        try:
            _set_status(
                book_id,
                locale,
                state="starting",
                phase="loading_tts",
                message="Loading text-to-speech pipeline…",
                started_at=time.time(),
                provider=provider_override or "auto",
            )
            from library.localization.selection import WorkloadHint
            from library.localization.tts.factory import get_tts_provider, synthesize_with_fallback

            try:
                _set_status(
                    book_id,
                    locale,
                    state="running",
                    phase="gpu_spinup",
                    message="Starting voice synthesis…",
                )
                tts = get_tts_provider(provider_override, workload=WorkloadHint.LONG_FORM)
            except ValueError:
                logger.exception("TTS provider init failed for book %d", book_id)
                _set_status(
                    book_id,
                    locale,
                    state="failed",
                    phase="error",
                    message="TTS provider failed to initialize.",
                    finished_at=time.time(),
                )
                return

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
                    if (
                        line.strip()
                        and not line.startswith("WEBVTT")
                        and "-->" not in line
                        and not line.strip().isdigit()
                    ):
                        lines.append(line.strip())

            if not lines:
                logger.error("No text found in VTT: %s", vtt_path)
                return

            # CJK languages don't use spaces between words/sentences
            lang_prefix = locale.split("-")[0].lower()
            joiner = "" if lang_prefix in ("zh", "ja", "ko") else " "
            full_text = joiner.join(lines)

            _set_status(
                book_id,
                locale,
                phase="synthesizing",
                message=f"Synthesizing audio with {tts.name}…",
                tts_provider=tts.name,
            )

            # Generate audio to a provider-appropriate intermediate format,
            # then transcode to Opus for consistency with the rest of the library.
            # edge-tts writes MP3; XTTS (RunPod + Vast.ai) writes WAV.
            output_dir = audio_file_path.parent / "translated"
            output_dir.mkdir(parents=True, exist_ok=True)
            intermediate_ext = "mp3" if tts.name == "edge-tts" else "wav"
            intermediate_path = (
                output_dir / f"{audio_file_path.stem}.{locale}.tts.{intermediate_ext}"
            )
            output_path = output_dir / f"{audio_file_path.stem}.{locale}.opus"

            # Wraps network errors once against edge-tts; ffmpeg below
            # sniffs content, so the extension mismatch on fallback is harmless.
            synthesize_with_fallback(tts, full_text, locale, voice, intermediate_path)

            _set_status(book_id, locale, phase="transcoding", message="Transcoding to Opus format…")
            import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names

            transcode = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
                [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(intermediate_path),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "64k",
                    "-vbr",
                    "on",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if transcode.returncode == 0:
                intermediate_path.unlink(missing_ok=True)
            else:
                logger.warning("Opus transcode failed, keeping source: %s", transcode.stderr[:200])
                output_path = intermediate_path

            # Get duration if possible
            duration = None
            try:
                import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names

                result = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
                    [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                        "ffprobe",
                        "-v",
                        "quiet",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "csv=p=0",
                        str(output_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration = float(result.stdout.strip())
            except Exception as e:
                logger.debug("ffprobe duration probe failed (non-fatal): %s", e)

            # Save to database
            gen_conn = sqlite3.connect(db_path)
            gen_conn.execute("PRAGMA journal_mode=WAL")
            gen_conn.execute("PRAGMA foreign_keys=ON")
            try:
                gen_conn.execute(
                    "INSERT OR REPLACE INTO chapter_translations_audio "
                    "(audiobook_id, chapter_index, locale, audio_path, "
                    " tts_provider, tts_voice, duration_seconds) "
                    "VALUES (?, 0, ?, ?, ?, ?, ?)",
                    (book_id, locale, str(output_path), tts.name, voice, duration),
                )
                gen_conn.commit()
                logger.info(
                    "Translated audio saved for book %d: %s (%.1fs)",
                    book_id,
                    output_path.name,
                    duration or 0,
                )
            finally:
                gen_conn.close()
            _set_status(
                book_id,
                locale,
                state="completed",
                phase="done",
                message="Translated audio ready.",
                finished_at=time.time(),
            )
        except Exception as e:
            logger.exception("TTS generation failed for book %d", book_id)
            _set_status(
                book_id,
                locale,
                state="failed",
                phase="error",
                message=(
                    "Audio generation failed. The GPU server may be "
                    "offline — please try again in a few minutes."
                ),
                error=str(e),
                finished_at=time.time(),
            )

    _set_status(
        book_id, locale, state="queued", phase="queued", message="Queued…", started_at=time.time()
    )
    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()

    return jsonify(
        {
            "audiobook_id": book_id,
            "locale": locale,
            "voice": voice,
            "status": "started",
            "message": "Translated audio generation started in background.",
        }
    )


@translated_audio_bp.route("/api/translated-audio/status/<int:book_id>/<locale>", methods=["GET"])
@guest_allowed
def get_tts_job_status(book_id, locale):
    """Poll the progress of a running TTS generation job."""
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


def _load_translated_audio_context(book_id, locale):
    """Return (vtt_path, audio_file_path) or a (response, status_code) error tuple."""
    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return (jsonify({"error": "Audiobook not found"}), 404)

        sub_row = conn.execute(
            "SELECT vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if not sub_row:
            return (jsonify({"error": "Translated subtitles required first."}), 400)

        existing_audio = conn.execute(
            "SELECT id FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if existing_audio:
            return (
                jsonify(
                    {
                        "audiobook_id": book_id,
                        "status": "exists",
                        "message": "Translated audio already exists.",
                    }
                ),
                200,
            )

        vtt_path = Path(sub_row["vtt_path"])
        audio_file_path = Path(book["file_path"])
    finally:
        conn.close()
    return (vtt_path, audio_file_path)


@translated_audio_bp.route("/api/user/translated-audio/request", methods=["POST"])
@guest_allowed
def user_request_translated_audio():
    """Allow a signed-in user to request translated audio generation.

    Rate-limited per (user, book) with a cooldown to prevent duplicate GPU jobs.
    Requires translated subtitles to exist (TTS reads the VTT for text input).
    """
    from flask import current_app

    auth_on = current_app.config.get("AUTH_ENABLED", False)
    user = getattr(g, "user", None)
    if auth_on and not user:
        return jsonify({"error": "Sign in to request translated audio"}), 401

    data = request.get_json(silent=True) or {}
    book_id = data.get("audiobook_id")
    locale = data.get("locale", "zh-Hans")
    if not book_id:
        return jsonify({"error": "audiobook_id is required"}), 400

    user_id = _extract_user_id(user)
    cooldown_resp = _check_user_cooldown(user_id, book_id)
    if cooldown_resp is not None:
        return cooldown_resp

    existing_job = _get_status(int(book_id), locale)
    if existing_job and existing_job.get("state") in ("queued", "starting", "running"):
        return jsonify(
            {"audiobook_id": book_id, "locale": locale, "status": "already_running", **existing_job}
        )

    loaded = _load_translated_audio_context(book_id, locale)
    if isinstance(loaded[1], int):
        return loaded
    vtt_path, audio_file_path = loaded

    db_path = str(_db_path)
    voice = "zh-CN-XiaoxiaoNeural"

    def _generate():
        try:
            _set_status(
                int(book_id),
                locale,
                state="starting",
                phase="loading_tts",
                message="Loading text-to-speech pipeline…",
                started_at=time.time(),
                provider="auto",
            )
            from library.localization.selection import WorkloadHint
            from library.localization.tts.factory import get_tts_provider, synthesize_with_fallback

            _set_status(
                int(book_id),
                locale,
                state="running",
                phase="gpu_spinup",
                message="Starting voice synthesis…",
            )
            tts = get_tts_provider(None, workload=WorkloadHint.LONG_FORM)

            if not vtt_path.exists():
                logger.error("VTT file missing: %s", vtt_path)
                _set_status(
                    int(book_id),
                    locale,
                    state="failed",
                    phase="error",
                    message="Subtitle file missing from disk.",
                    finished_at=time.time(),
                )
                return

            vtt_text = vtt_path.read_text(encoding="utf-8")
            lines = []
            for block in vtt_text.split("\n\n"):
                block_lines = block.strip().split("\n")
                for line in block_lines:
                    if (
                        line.strip()
                        and not line.startswith("WEBVTT")
                        and "-->" not in line
                        and not line.strip().isdigit()
                    ):
                        lines.append(line.strip())

            if not lines:
                logger.error("No text found in VTT: %s", vtt_path)
                _set_status(
                    int(book_id),
                    locale,
                    state="failed",
                    phase="error",
                    message="No text found in subtitle file.",
                    finished_at=time.time(),
                )
                return

            lang_prefix = locale.split("-")[0].lower()
            joiner = "" if lang_prefix in ("zh", "ja", "ko") else " "
            full_text = joiner.join(lines)

            _set_status(
                int(book_id),
                locale,
                phase="synthesizing",
                message=f"Synthesizing audio with {tts.name}…",
                tts_provider=tts.name,
            )

            output_dir = audio_file_path.parent / "translated"
            output_dir.mkdir(parents=True, exist_ok=True)
            intermediate_ext = "mp3" if tts.name == "edge-tts" else "wav"
            intermediate_path = (
                output_dir / f"{audio_file_path.stem}.{locale}.tts.{intermediate_ext}"
            )
            output_path = output_dir / f"{audio_file_path.stem}.{locale}.opus"

            synthesize_with_fallback(tts, full_text, locale, voice, intermediate_path)

            _set_status(
                int(book_id), locale, phase="transcoding", message="Transcoding to Opus format…"
            )
            import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names

            transcode = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
                [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(intermediate_path),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "64k",
                    "-vbr",
                    "on",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if transcode.returncode == 0:
                intermediate_path.unlink(missing_ok=True)
            else:
                logger.warning("Opus transcode failed, keeping source: %s", transcode.stderr[:200])
                output_path = intermediate_path

            duration = None
            try:
                result = subprocess.run(  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input  # nosec B607,B603 — partial path — system tools (ffmpeg, systemctl, etc.) must be on PATH for cross-distro compatibility
                    [  # noqa: S603,S607 — system-installed tool; args are config-controlled or hardcoded constants, not user input
                        "ffprobe",
                        "-v",
                        "quiet",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "csv=p=0",
                        str(output_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration = float(result.stdout.strip())
            except Exception as e:
                logger.debug("ffprobe duration probe failed (non-fatal): %s", e)

            _set_status(int(book_id), locale, phase="saving", message="Saving translated audio…")
            gen_conn = sqlite3.connect(db_path)
            gen_conn.execute("PRAGMA journal_mode=WAL")
            gen_conn.execute("PRAGMA foreign_keys=ON")
            try:
                gen_conn.execute(
                    "INSERT OR REPLACE INTO chapter_translations_audio "
                    "(audiobook_id, chapter_index, locale, audio_path, "
                    " tts_provider, tts_voice, duration_seconds) "
                    "VALUES (?, 0, ?, ?, ?, ?, ?)",
                    (int(book_id), locale, str(output_path), tts.name, voice, duration),
                )
                gen_conn.commit()
            finally:
                gen_conn.close()
            _set_status(
                int(book_id),
                locale,
                state="completed",
                phase="done",
                message="Translated audio ready.",
                finished_at=time.time(),
            )
        except Exception as e:
            logger.exception("User-requested TTS generation failed for book %s", book_id)
            _set_status(
                int(book_id),
                locale,
                state="failed",
                phase="error",
                message=(
                    "Audio generation failed. The GPU server may be "
                    "offline — please try again in a few minutes."
                ),
                error=str(e),
                finished_at=time.time(),
            )

    _set_status(
        int(book_id),
        locale,
        state="queued",
        phase="queued",
        message="Queued…",
        started_at=time.time(),
    )
    threading.Thread(target=_generate, daemon=True).start()

    return jsonify(
        {
            "audiobook_id": book_id,
            "locale": locale,
            "status": "started",
            "message": (
                "Translated audio generation started. This may take several "
                "minutes — the GPU server has to spin up first."
            ),
        }
    )
