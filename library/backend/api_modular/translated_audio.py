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
            return jsonify(
                {
                    "error": "Translated subtitles not found. "
                    "Generate subtitles first via POST /api/subtitles/generate",
                }
            ), 400

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
            from library.localization.tts.factory import (
                get_tts_provider,
                synthesize_with_fallback,
            )

            try:
                _set_status(
                    book_id,
                    locale,
                    state="running",
                    phase="gpu_spinup",
                    message=(
                        "Waking up the GPU server. Cold starts can take a "
                        "minute or two…"
                    ),
                )
                tts = get_tts_provider(
                    provider_override, workload=WorkloadHint.LONG_FORM
                )
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

            _set_status(
                book_id,
                locale,
                phase="transcoding",
                message="Transcoding to Opus format…",
            )
            import subprocess

            transcode = subprocess.run(
                [
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
                logger.warning(
                    "Opus transcode failed, keeping source: %s", transcode.stderr[:200]
                )
                output_path = intermediate_path

            # Get duration if possible
            duration = None
            try:
                import subprocess

                result = subprocess.run(
                    [
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
        book_id,
        locale,
        state="queued",
        phase="queued",
        message="Queued…",
        started_at=time.time(),
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


@translated_audio_bp.route(
    "/api/translated-audio/status/<int:book_id>/<locale>",
    methods=["GET"],
)
@guest_allowed
def get_tts_job_status(book_id, locale):
    """Poll the progress of a running TTS generation job."""
    status = _get_status(book_id, locale)
    if not status:
        return jsonify({"state": "idle"})
    return jsonify(
        {
            "audiobook_id": book_id,
            "locale": locale,
            **status,
        }
    )


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

    user_id = None
    if user is not None:
        user_id = (
            user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
        )
    if user_id is not None:
        key = (int(user_id), int(book_id))
        now = time.time()
        last = _user_requests.get(key, 0)
        if now - last < _USER_COOLDOWN_SEC:
            return jsonify(
                {
                    "status": "cooldown",
                    "message": "Please wait a moment before trying again.",
                    "retry_after": int(_USER_COOLDOWN_SEC - (now - last)),
                }
            ), 429
        _user_requests[key] = now

    existing_job = _get_status(int(book_id), locale)
    if existing_job and existing_job.get("state") in ("queued", "starting", "running"):
        return jsonify(
            {
                "audiobook_id": book_id,
                "locale": locale,
                "status": "already_running",
                **existing_job,
            }
        )

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        if not book:
            return jsonify({"error": "Audiobook not found"}), 404

        sub_row = conn.execute(
            "SELECT vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if not sub_row:
            return jsonify(
                {
                    "error": "Translated subtitles required first.",
                }
            ), 400

        existing_audio = conn.execute(
            "SELECT id FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND chapter_index = 0 AND locale = ?",
            (book_id, locale),
        ).fetchone()
        if existing_audio:
            return jsonify(
                {
                    "audiobook_id": book_id,
                    "status": "exists",
                    "message": "Translated audio already exists.",
                }
            )

        vtt_path = Path(sub_row["vtt_path"])
        audio_file_path = Path(book["file_path"])
    finally:
        conn.close()

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
            from library.localization.tts.factory import (
                get_tts_provider,
                synthesize_with_fallback,
            )

            _set_status(
                int(book_id),
                locale,
                state="running",
                phase="gpu_spinup",
                message=(
                    "Waking up the GPU server. Cold starts can take a minute or two…"
                ),
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
                int(book_id),
                locale,
                phase="transcoding",
                message="Transcoding to Opus format…",
            )
            import subprocess

            transcode = subprocess.run(
                [
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
                logger.warning(
                    "Opus transcode failed, keeping source: %s",
                    transcode.stderr[:200],
                )
                output_path = intermediate_path

            duration = None
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
                        str(output_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration = float(result.stdout.strip())
            except Exception:
                pass

            _set_status(
                int(book_id),
                locale,
                phase="saving",
                message="Saving translated audio…",
            )
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
            logger.exception(
                "User-requested TTS generation failed for book %s",
                book_id,
            )
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
