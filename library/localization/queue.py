"""Background translation queue.

Processes books through the full localization pipeline:
STT transcription → subtitle translation → TTS narration.

Triggered automatically by:
- Book scan/import (for all configured non-English locales)
- User locale change (for books missing that locale's assets)
- Manual admin API call

The queue is persistent (SQLite-backed), resumable (skips completed
steps), and processes one book at a time to manage GPU costs.
"""

import logging
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_queue_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_shutdown_event = threading.Event()
_db_path: Path | None = None
_library_path: Path | None = None

# Current job status — visible to the frontend via the status API
_current_status: dict = {}


def init_queue(database_path: Path, library_path: Path) -> None:
    global _db_path, _library_path
    _db_path = database_path
    _library_path = library_path
    _ensure_queue_table()
    _recover_stale_jobs()


def _get_db() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError(
            "Localization queue is not initialized — call init_queue(database_path, "
            "library_path) before enqueueing or reading jobs."
        )
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_queue_table() -> None:
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translation_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audiobook_id INTEGER NOT NULL,
                locale TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                state TEXT DEFAULT 'pending',
                step TEXT DEFAULT 'stt',
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                last_progress_at TIMESTAMP,
                total_chapters INTEGER,
                UNIQUE(audiobook_id, locale),
                FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
            )
        """)
        # In-place ALTER for upgraded DBs that pre-date these columns. This
        # MUST run before the CREATE INDEX on last_progress_at — otherwise a
        # legacy DB whose translation_queue pre-dates the column errors out
        # on the index creation before the ALTER can fix the schema.
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(translation_queue)").fetchall()
        }
        if "last_progress_at" not in existing_cols:
            conn.execute("ALTER TABLE translation_queue ADD COLUMN last_progress_at TIMESTAMP")
            conn.execute(
                "UPDATE translation_queue SET last_progress_at = COALESCE(started_at, created_at)"
            )
        if "total_chapters" not in existing_cols:
            conn.execute("ALTER TABLE translation_queue ADD COLUMN total_chapters INTEGER")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tq_state
            ON translation_queue(state, priority DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tq_last_progress
            ON translation_queue(last_progress_at)
        """)
        conn.commit()
    finally:
        conn.close()


def _recover_stale_jobs() -> None:
    """Reset jobs left in 'processing' state from a prior crash/restart.

    Does NOT auto-start the worker — recovered jobs wait in 'pending'
    until explicitly triggered (user action or admin API).  Auto-starting
    under gevent's single worker blocks the entire API.
    """
    conn = _get_db()
    try:
        updated = conn.execute(
            "UPDATE translation_queue SET state = 'pending', started_at = NULL "
            "WHERE state = 'processing'"
        ).rowcount
        conn.commit()
        if updated:
            logger.info("Recovered %d stale processing jobs to pending", updated)
    finally:
        conn.close()


def enqueue(
    audiobook_id: int, locale: str, priority: int = 0, *, start_worker: bool = False
) -> None:
    """Add a book+locale to the translation queue. Idempotent.

    Does NOT auto-start the worker by default — under gevent's single
    worker, starting the translation thread blocks the entire API.
    Pass ``start_worker=True`` only from explicit user-triggered endpoints.
    """
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO translation_queue "
            "(audiobook_id, locale, priority) VALUES (?, ?, ?)",
            (audiobook_id, locale, priority),
        )
        conn.commit()
    finally:
        conn.close()
    if start_worker:
        _ensure_worker()


def enqueue_book_all_locales(audiobook_id: int, priority: int = 0) -> None:
    """Queue a book for translation in all configured non-English locales."""
    from .config import SUPPORTED_LOCALES

    locales = [loc for loc in SUPPORTED_LOCALES if loc != "en"]
    if not locales:
        return
    conn = _get_db()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO translation_queue "
            "(audiobook_id, locale, priority) VALUES (?, ?, ?)",
            [(audiobook_id, loc, priority) for loc in locales],
        )
        conn.commit()
    finally:
        conn.close()


def enqueue_all_books_for_locale(locale: str, priority: int = 0) -> int:
    """Queue all books missing translations for a locale.

    Uses a single INSERT … SELECT to avoid opening/closing 1800+
    individual connections, which blocks the gevent worker for seconds.
    Returns the number of rows inserted.
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO translation_queue "
            "(audiobook_id, locale, priority) "
            "SELECT a.id, ?, ? FROM audiobooks a "
            "WHERE a.id NOT IN ("
            "  SELECT cs.audiobook_id FROM chapter_subtitles cs "
            "  WHERE cs.locale = 'en' "
            "  GROUP BY cs.audiobook_id"
            ")",
            (locale, priority),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def start_processing() -> None:
    """Explicitly start the queue worker.

    Call this ONLY from user-triggered endpoints (e.g., "Generate narration"
    button).  Never from background import/scan paths — those just enqueue.
    """
    _ensure_worker()


def bump_priority(audiobook_id: int, locale: str, priority: int = 100) -> None:
    """Move a book to the front of the queue (e.g., user just opened it)."""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE translation_queue SET priority = MAX(priority, ?) "
            "WHERE audiobook_id = ? AND locale = ? AND state = 'pending'",
            (priority, audiobook_id, locale),
        )
        conn.commit()
    finally:
        conn.close()


def get_queue_status() -> dict:
    """Return summary of queue state."""
    conn = _get_db()
    try:
        counts = {}
        for row in conn.execute(
            "SELECT state, COUNT(*) as cnt FROM translation_queue GROUP BY state"
        ).fetchall():
            counts[row["state"]] = row["cnt"]
        return {
            "pending": counts.get("pending", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "current": dict(_current_status) if _current_status else None,
        }
    finally:
        conn.close()


def get_book_translation_status(audiobook_id: int, locale: str) -> dict | None:
    """Return translation status for a specific book+locale."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM translation_queue WHERE audiobook_id = ? AND locale = ?",
            (audiobook_id, locale),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if (
            result["state"] == "processing"
            and _current_status.get("audiobook_id") == audiobook_id
            and _current_status.get("locale") == locale
        ):
            result.update(_current_status)
        return result
    finally:
        conn.close()


def _ensure_worker() -> None:
    global _worker_thread
    with _queue_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _shutdown_event.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="translation-queue"
        )
        _worker_thread.start()
        logger.info("Translation queue worker started")


def _worker_loop() -> None:
    while not _shutdown_event.is_set():
        job = _next_job()
        if not job:
            time.sleep(10)
            continue
        _process_job(job)


def _next_job() -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM translation_queue "
            "WHERE state = 'pending' "
            "ORDER BY priority DESC, created_at ASC "
            "LIMIT 1"
        ).fetchone()
        if not row:
            return None
        job = dict(row)
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        conn.execute(
            "UPDATE translation_queue "
            "SET state = 'processing', started_at = ?, last_progress_at = ? "
            "WHERE id = ?",
            (now, now, job["id"]),
        )
        conn.commit()
        return job
    finally:
        conn.close()


def _set_current(audiobook_id: int, locale: str, **fields) -> None:
    global _current_status
    _current_status = {
        "audiobook_id": audiobook_id,
        "locale": locale,
        "updated_at": time.time(),
        **fields,
    }
    step = fields.get("step")
    if step:
        try:
            conn = _get_db()
            conn.execute(
                "UPDATE translation_queue "
                "SET step = ?, last_progress_at = CURRENT_TIMESTAMP "
                "WHERE audiobook_id = ? AND locale = ? AND state = 'processing'",
                (step, audiobook_id, locale),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("step heartbeat update failed (non-fatal): %s", e)


def _load_book_state(book_id: int, locale: str):
    """Fetch the book row + existing chapter-coverage sets. Returns
    (book, audio_path, existing_en, existing_tr, has_tts, err_message_or_None).
    """
    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()
        if not book:
            return None, None, set(), set(), None, "Book not found in DB"
        audio_path = Path(book["file_path"])
        if not audio_path.exists():
            return None, None, set(), set(), None, "Audio file not found on disk"

        existing_en = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT chapter_index FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = 'en'",
                (book_id,),
            ).fetchall()
        }
        existing_tr = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT chapter_index FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = ?",
                (book_id, locale),
            ).fetchall()
        }
        has_tts = conn.execute(
            "SELECT id FROM chapter_translations_audio WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        ).fetchone()
        return book, audio_path, existing_en, existing_tr, has_tts, None
    finally:
        conn.close()


def _run_stt_phase(book, locale, audio_path, existing_en, existing_tr, resume_step):
    """Run (or skip) the STT/subtitle phase based on prior coverage."""
    if resume_step != "stt":
        logger.info("Book %d: resuming from step '%s', skipping STT", book["id"], resume_step)
        return

    if not existing_en:
        _set_current(
            book["id"],
            locale,
            step="stt",
            phase="starting",
            message=f"Transcribing: {book['title']}",
        )
        _run_stt_and_translate(book["id"], locale, audio_path, set())
    elif len(existing_tr) < len(existing_en):
        _set_current(
            book["id"],
            locale,
            step="stt",
            phase="resuming",
            message=f"Resuming transcription: {book['title']}",
        )
        _run_stt_and_translate(book["id"], locale, audio_path, existing_en)
    else:
        logger.info("Book %d: subtitles already complete for %s", book["id"], locale)


def _run_tts_phase(book, locale, audio_path, has_tts):
    """Run (or skip) the TTS narration phase."""
    if has_tts:
        logger.info("Book %d: TTS audio already exists for %s", book["id"], locale)
        return
    _set_current(
        book["id"],
        locale,
        step="tts",
        phase="starting",
        message=f"Generating narration: {book['title']}",
    )
    _run_tts(book["id"], locale, audio_path)


def _process_job(job: dict) -> None:
    book_id = job["audiobook_id"]
    locale = job["locale"]
    book, audio_path, existing_en, existing_tr, has_tts, err = _load_book_state(book_id, locale)
    if err:
        _finish_job(job["id"], "failed", error=err)
        return

    resume_step = job.get("step", "stt")
    try:
        _run_stt_phase(book, locale, audio_path, existing_en, existing_tr, resume_step)
        _run_tts_phase(book, locale, audio_path, has_tts)
        _finish_job(job["id"], "completed")
        logger.info("Book %d translation complete for %s", book_id, locale)
    except Exception as e:
        logger.exception("Translation failed for book %d locale %s", book_id, locale)
        _finish_job(job["id"], "failed", error=str(e))


def _run_stt_and_translate(
    book_id: int, locale: str, audio_path: Path, skip_chapters: set[int]
) -> None:
    """Run STT transcription and subtitle generation."""
    from .pipeline import generate_book_subtitles, get_stt_provider
    from .selection import WorkloadHint

    subtitle_dir = audio_path.parent / "subtitles"
    subtitle_dir.mkdir(parents=True, exist_ok=True)

    _set_current(book_id, locale, phase="loading_stt", message="Loading speech-to-text pipeline…")

    stt = get_stt_provider("", workload=WorkloadHint.LONG_FORM)

    _set_current(
        book_id,
        locale,
        phase="transcribing",
        message=f"Transcribing with {stt.name}…",
        stt_provider=stt.name,
    )

    db_path = str(_db_path)

    def _on_progress(ch_idx: int, total: int, title: str):
        _set_current(
            book_id,
            locale,
            phase="transcribing",
            message=f"Chapter {ch_idx + 1}/{total}: {title}",
            chapter_index=ch_idx,
            chapter_total=total,
        )
        try:
            hb_conn = _get_db()
            hb_conn.execute(
                "UPDATE translation_queue "
                "SET last_progress_at = CURRENT_TIMESTAMP, total_chapters = ? "
                "WHERE audiobook_id = ? AND locale = ? AND state = 'processing'",
                (total, book_id, locale),
            )
            hb_conn.commit()
            hb_conn.close()
        except Exception as e:
            logger.debug("chapter-count heartbeat update failed (non-fatal): %s", e)

    gen_conn = sqlite3.connect(db_path)
    gen_conn.execute("PRAGMA journal_mode=WAL")
    gen_conn.execute("PRAGMA foreign_keys=ON")
    try:

        def _on_chapter_complete(ch_idx: int, source_vtt: Path, translated_vtt: Path | None):
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

        generate_book_subtitles(
            audio_path=audio_path,
            output_dir=subtitle_dir,
            target_locale=locale,
            stt_provider=stt,
            on_progress=_on_progress,
            on_chapter_complete=_on_chapter_complete,
            skip_chapters=skip_chapters,
        )
    finally:
        gen_conn.close()


def _load_vtt_rows(book_id: int, locale: str):
    """Fetch ordered (chapter_index, vtt_path) rows for a locale."""
    conn = _get_db()
    try:
        return conn.execute(
            "SELECT chapter_index, vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND locale = ? "
            "ORDER BY chapter_index",
            (book_id, locale),
        ).fetchall()
    finally:
        conn.close()


def _read_vtt_lines(vtt_path: Path) -> list[str]:
    """Read a VTT and return all caption lines (no WEBVTT header, no
    timestamps, no cue numbers, no blank lines).
    """
    vtt_text = vtt_path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    for block in vtt_text.split("\n\n"):
        for line in block.strip().split("\n"):
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("WEBVTT")
                and "-->" not in stripped
                and not stripped.isdigit()
            ):
                lines.append(stripped)
    return lines


def _join_caption_text(lines: list[str], locale: str) -> str:
    """Join caption lines with locale-appropriate whitespace."""
    lang_prefix = locale.split("-")[0].lower()
    joiner = "" if lang_prefix in ("zh", "ja", "ko") else " "
    return joiner.join(lines)


def _transcode_to_opus(intermediate_path: Path, output_path: Path) -> Path:
    """Transcode a TTS intermediate file to Opus. On failure, return the
    original intermediate path (so we still have playable audio).
    """
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
        check=False,
    )
    if transcode.returncode == 0:
        intermediate_path.unlink(missing_ok=True)
        return output_path
    logger.warning("Opus transcode failed: %s", transcode.stderr[:200])
    return intermediate_path


def _probe_duration(audio_path: Path) -> float | None:
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
    except Exception:
        return None
    return None


def _persist_tts_chapter(
    db_path: str,
    book_id: int,
    ch_idx: int,
    locale: str,
    output_path: Path,
    tts_name: str,
    voice: str,
    duration: float | None,
) -> None:
    """Insert/replace a chapter_translations_audio row."""
    gen_conn = sqlite3.connect(db_path)
    gen_conn.execute("PRAGMA journal_mode=WAL")
    gen_conn.execute("PRAGMA foreign_keys=ON")
    try:
        gen_conn.execute(
            "INSERT OR REPLACE INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, "
            " tts_provider, tts_voice, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, ch_idx, locale, str(output_path), tts_name, voice, duration),
        )
        gen_conn.commit()
    finally:
        gen_conn.close()


def _tts_one_chapter(
    row,
    book_id: int,
    locale: str,
    audio_path: Path,
    output_dir: Path,
    total_rows: int,
    tts,
    voice: str,
    db_path: str,
) -> None:
    """Synthesize + persist a single chapter's TTS output."""
    ch_idx = row["chapter_index"]
    vtt_path = Path(row["vtt_path"])

    _set_current(
        book_id,
        locale,
        step="tts",
        phase="synthesizing",
        message=f"Narrating chapter {ch_idx + 1}/{total_rows}",
        chapter_index=ch_idx,
        chapter_total=total_rows,
    )

    if not vtt_path.exists():
        logger.warning("VTT missing for chapter %d: %s", ch_idx, vtt_path)
        return

    lines = _read_vtt_lines(vtt_path)
    if not lines:
        return
    full_text = _join_caption_text(lines, locale)

    from .tts.factory import synthesize_with_fallback

    intermediate_ext = "mp3" if tts.name == "edge-tts" else "wav"
    stem = f"{audio_path.stem}.ch{ch_idx:03d}.{locale}"
    intermediate_path = output_dir / f"{stem}.tts.{intermediate_ext}"
    output_path = output_dir / f"{stem}.opus"

    synthesize_with_fallback(tts, full_text, locale, voice, intermediate_path)
    output_path = _transcode_to_opus(intermediate_path, output_path)
    duration = _probe_duration(output_path)
    _persist_tts_chapter(db_path, book_id, ch_idx, locale, output_path, tts.name, voice, duration)


def _run_tts(book_id: int, locale: str, audio_path: Path) -> None:
    """Run TTS narration from translated subtitle text."""
    from .config import TTS_VOICE_ZH
    from .selection import WorkloadHint
    from .tts.factory import get_tts_provider

    vtt_rows = _load_vtt_rows(book_id, locale)
    if not vtt_rows:
        logger.warning(
            "No translated subtitles for book %d locale %s — skipping TTS", book_id, locale
        )
        return

    _set_current(
        book_id, locale, step="tts", phase="loading_tts", message="Loading text-to-speech pipeline…"
    )

    tts = get_tts_provider(None, workload=WorkloadHint.LONG_FORM)
    voice = TTS_VOICE_ZH
    output_dir = audio_path.parent / "translated"
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(_db_path)

    for row in vtt_rows:
        _tts_one_chapter(
            row, book_id, locale, audio_path, output_dir, len(vtt_rows), tts, voice, db_path
        )

    logger.info(
        "TTS narration complete for book %d locale %s: %d chapters", book_id, locale, len(vtt_rows)
    )


def _finish_job(job_id: int, state: str, error: str | None = None) -> None:
    global _current_status
    conn = _get_db()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        conn.execute(
            "UPDATE translation_queue "
            "SET state = ?, error = ?, finished_at = ?, last_progress_at = ? "
            "WHERE id = ?",
            (state, error, now, now, job_id),
        )
        conn.commit()
    finally:
        conn.close()
    _current_status = {}


def shutdown() -> None:
    _shutdown_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=5)
