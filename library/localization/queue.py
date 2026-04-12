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
                UNIQUE(audiobook_id, locale),
                FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tq_state
            ON translation_queue(state, priority DESC)
        """)
        conn.commit()
    finally:
        conn.close()


def _recover_stale_jobs() -> None:
    """Reset jobs left in 'processing' state from a prior crash/restart."""
    conn = _get_db()
    try:
        updated = conn.execute(
            "UPDATE translation_queue SET state = 'pending', started_at = NULL "
            "WHERE state = 'processing'",
        ).rowcount
        conn.commit()
        if updated:
            logger.info("Recovered %d stale processing jobs to pending", updated)
            _ensure_worker()
    finally:
        conn.close()


def enqueue(audiobook_id: int, locale: str, priority: int = 0) -> None:
    """Add a book+locale to the translation queue. Idempotent."""
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
    _ensure_worker()


def enqueue_book_all_locales(audiobook_id: int, priority: int = 0) -> None:
    """Queue a book for translation in all configured non-English locales."""
    from .config import SUPPORTED_LOCALES
    for locale in SUPPORTED_LOCALES:
        if locale != "en":
            enqueue(audiobook_id, locale, priority)


def enqueue_all_books_for_locale(locale: str, priority: int = 0) -> None:
    """Queue all books missing translations for a locale."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT a.id FROM audiobooks a "
            "WHERE a.id NOT IN ("
            "  SELECT cs.audiobook_id FROM chapter_subtitles cs "
            "  WHERE cs.locale = 'en' "
            "  GROUP BY cs.audiobook_id"
            ")",
        ).fetchall()
        for row in rows:
            enqueue(row["id"], locale, priority)
    finally:
        conn.close()


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
            "SELECT * FROM translation_queue "
            "WHERE audiobook_id = ? AND locale = ?",
            (audiobook_id, locale),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if (result["state"] == "processing"
                and _current_status.get("audiobook_id") == audiobook_id
                and _current_status.get("locale") == locale):
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
            target=_worker_loop, daemon=True, name="translation-queue",
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
            "LIMIT 1",
        ).fetchone()
        if not row:
            return None
        job = dict(row)
        conn.execute(
            "UPDATE translation_queue SET state = 'processing', started_at = ? "
            "WHERE id = ?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), job["id"]),
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


def _process_job(job: dict) -> None:
    book_id = job["audiobook_id"]
    locale = job["locale"]

    conn = _get_db()
    try:
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        if not book:
            _finish_job(job["id"], "failed", error="Book not found in DB")
            return
        audio_path = Path(book["file_path"])
        if not audio_path.exists():
            _finish_job(job["id"], "failed", error="Audio file not found on disk")
            return

        existing_en = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT chapter_index FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = 'en'",
                (book_id,),
            ).fetchall()
        }
        existing_tr = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT chapter_index FROM chapter_subtitles "
                "WHERE audiobook_id = ? AND locale = ?",
                (book_id, locale),
            ).fetchall()
        }
        has_tts = conn.execute(
            "SELECT id FROM chapter_translations_audio "
            "WHERE audiobook_id = ? AND locale = ?",
            (book_id, locale),
        ).fetchone()
    finally:
        conn.close()

    try:
        # Step 1: STT + subtitle translation
        if not existing_en or len(existing_en) == 0:
            _set_current(book_id, locale, step="stt", phase="starting",
                         message=f"Transcribing: {book['title']}")
            _run_stt_and_translate(book_id, locale, audio_path, set())
        elif len(existing_tr) < len(existing_en):
            _set_current(book_id, locale, step="stt", phase="resuming",
                         message=f"Resuming transcription: {book['title']}")
            _run_stt_and_translate(book_id, locale, audio_path, existing_en)
        else:
            logger.info("Book %d: subtitles already complete for %s", book_id, locale)

        # Step 2: TTS narration
        if not has_tts:
            _set_current(book_id, locale, step="tts", phase="starting",
                         message=f"Generating narration: {book['title']}")
            _run_tts(book_id, locale, audio_path)
        else:
            logger.info("Book %d: TTS audio already exists for %s", book_id, locale)

        _finish_job(job["id"], "completed")
        logger.info("Book %d translation complete for %s", book_id, locale)

    except Exception as e:
        logger.exception("Translation failed for book %d locale %s", book_id, locale)
        _finish_job(job["id"], "failed", error=str(e))


def _run_stt_and_translate(
    book_id: int, locale: str, audio_path: Path, skip_chapters: set[int],
) -> None:
    """Run STT transcription and subtitle generation."""
    from .pipeline import generate_book_subtitles, get_stt_provider
    from .selection import WorkloadHint

    subtitle_dir = audio_path.parent / "subtitles"
    subtitle_dir.mkdir(parents=True, exist_ok=True)

    _set_current(book_id, locale, phase="loading_stt",
                 message="Loading speech-to-text pipeline…")

    stt = get_stt_provider("", workload=WorkloadHint.LONG_FORM)

    _set_current(book_id, locale, phase="transcribing",
                 message=f"Transcribing with {stt.name}…",
                 stt_provider=stt.name)

    db_path = str(_db_path)

    def _on_progress(ch_idx: int, total: int, title: str):
        _set_current(book_id, locale, phase="transcribing",
                     message=f"Chapter {ch_idx + 1}/{total}: {title}",
                     chapter_index=ch_idx, chapter_total=total)

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


def _run_tts(book_id: int, locale: str, audio_path: Path) -> None:
    """Run TTS narration from translated subtitle text."""
    from .config import TTS_VOICE_ZH
    from .selection import WorkloadHint
    from .tts.factory import get_tts_provider, synthesize_with_fallback

    conn = _get_db()
    try:
        vtt_rows = conn.execute(
            "SELECT chapter_index, vtt_path FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND locale = ? "
            "ORDER BY chapter_index",
            (book_id, locale),
        ).fetchall()
    finally:
        conn.close()

    if not vtt_rows:
        logger.warning("No translated subtitles for book %d locale %s — skipping TTS", book_id, locale)
        return

    _set_current(book_id, locale, step="tts", phase="loading_tts",
                 message="Loading text-to-speech pipeline…")

    tts = get_tts_provider(None, workload=WorkloadHint.LONG_FORM)
    voice = TTS_VOICE_ZH
    output_dir = audio_path.parent / "translated"
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = str(_db_path)

    for row in vtt_rows:
        ch_idx = row["chapter_index"]
        vtt_path = Path(row["vtt_path"])

        _set_current(book_id, locale, step="tts", phase="synthesizing",
                     message=f"Narrating chapter {ch_idx + 1}/{len(vtt_rows)}",
                     chapter_index=ch_idx, chapter_total=len(vtt_rows))

        if not vtt_path.exists():
            logger.warning("VTT missing for chapter %d: %s", ch_idx, vtt_path)
            continue

        vtt_text = vtt_path.read_text(encoding="utf-8")
        lines = []
        for block in vtt_text.split("\n\n"):
            for line in block.strip().split("\n"):
                if (line.strip()
                        and not line.startswith("WEBVTT")
                        and "-->" not in line
                        and not line.strip().isdigit()):
                    lines.append(line.strip())

        if not lines:
            continue

        lang_prefix = locale.split("-")[0].lower()
        joiner = "" if lang_prefix in ("zh", "ja", "ko") else " "
        full_text = joiner.join(lines)

        intermediate_ext = "mp3" if tts.name == "edge-tts" else "wav"
        stem = f"{audio_path.stem}.ch{ch_idx:03d}.{locale}"
        intermediate_path = output_dir / f"{stem}.tts.{intermediate_ext}"
        output_path = output_dir / f"{stem}.opus"

        synthesize_with_fallback(tts, full_text, locale, voice, intermediate_path)

        transcode = subprocess.run(
            ["ffmpeg", "-y", "-i", str(intermediate_path), "-c:a", "libopus",
             "-b:a", "64k", "-vbr", "on", str(output_path)],
            capture_output=True, text=True, timeout=300,
        )
        if transcode.returncode == 0:
            intermediate_path.unlink(missing_ok=True)
        else:
            logger.warning("Opus transcode failed: %s", transcode.stderr[:200])
            output_path = intermediate_path

        duration = None
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(output_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                duration = float(result.stdout.strip())
        except Exception:
            pass

        gen_conn = sqlite3.connect(db_path)
        gen_conn.execute("PRAGMA journal_mode=WAL")
        gen_conn.execute("PRAGMA foreign_keys=ON")
        try:
            gen_conn.execute(
                "INSERT OR REPLACE INTO chapter_translations_audio "
                "(audiobook_id, chapter_index, locale, audio_path, "
                " tts_provider, tts_voice, duration_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (book_id, ch_idx, locale, str(output_path), tts.name, voice, duration),
            )
            gen_conn.commit()
        finally:
            gen_conn.close()

    logger.info("TTS narration complete for book %d locale %s: %d chapters",
                book_id, locale, len(vtt_rows))


def _finish_job(job_id: int, state: str, error: str | None = None) -> None:
    global _current_status
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE translation_queue SET state = ?, error = ?, finished_at = ? "
            "WHERE id = ?",
            (state, error, time.strftime("%Y-%m-%d %H:%M:%S"), job_id),
        )
        conn.commit()
    finally:
        conn.close()
    _current_status = {}


def shutdown() -> None:
    _shutdown_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=5)
