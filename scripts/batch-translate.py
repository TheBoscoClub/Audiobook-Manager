#!/usr/bin/env python3
"""Standalone batch translation processor.

Runs the STT -> subtitle translation -> TTS pipeline independently of
the API process. This avoids blocking the web UI while processing.

Usage:
    /opt/audiobooks/library/venv/bin/python scripts/batch-translate.py \
        --db $AUDIOBOOKS_VAR_DIR/db/audiobooks.db \
        --library $AUDIOBOOKS_LIBRARY

    # Process a single book first:
    ... --book-id 114203

    # Dry run (show what would be processed):
    ... --dry-run
"""

import argparse
import logging
import os
import signal
import sqlite3
import subprocess  # nosec B404 — import subprocess — subprocess usage is intentional; all calls use hardcoded system tool names
import sys
import time
from pathlib import Path

# Add the library directory to the path so localization modules are importable
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
LIB_DIR = PROJECT_DIR / "library"
sys.path.insert(0, str(LIB_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch-translate")

# Graceful shutdown
_shutdown = False


def _signal_handler(_sig, _frame):
    global _shutdown
    logger.info("Shutdown signal received — finishing current book then exiting")
    _shutdown = True


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_tables(db_path: str) -> None:
    conn = get_db(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chapter_translations_audio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            audio_path TEXT NOT NULL,
            tts_provider TEXT,
            tts_voice TEXT,
            duration_seconds REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(audiobook_id, chapter_index, locale),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def next_pending_job(db_path: str, book_id: int | None = None) -> dict | None:
    """Atomically claim the next pending job.

    Uses UPDATE ... RETURNING so concurrent workers never grab the same row.
    Requires SQLite >= 3.35 (2021-03). Claim is serialised by SQLite's
    writer lock under WAL, so only one worker wins per row.
    """
    conn = get_db(db_path)
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        if book_id:
            row = conn.execute(
                "UPDATE translation_queue "
                "SET state = 'processing', started_at = ?, last_progress_at = ? "
                "WHERE id = (SELECT id FROM translation_queue "
                "            WHERE audiobook_id = ? AND state = 'pending' "
                "            LIMIT 1) "
                "RETURNING *",
                (now, now, book_id),
            ).fetchone()
        else:
            row = conn.execute(
                "UPDATE translation_queue "
                "SET state = 'processing', started_at = ?, last_progress_at = ? "
                "WHERE id = (SELECT id FROM translation_queue "
                "            WHERE state = 'pending' "
                "            ORDER BY priority DESC, created_at ASC LIMIT 1) "
                "RETURNING *",
                (now, now),
            ).fetchone()
        conn.commit()
        return dict(row) if row else None
    finally:
        conn.close()


def finish_job(db_path: str, job_id: int, state: str, error: str | None = None) -> None:
    conn = get_db(db_path)
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


def process_book_stt(db_path: str, book_id: int, locale: str, audio_path: Path) -> bool:
    """Run STT + subtitle translation for a single book. Returns True on success."""
    from localization.pipeline import generate_book_subtitles, get_stt_provider
    from localization.selection import WorkloadHint

    subtitle_dir = audio_path.parent / "subtitles"
    subtitle_dir.mkdir(parents=True, exist_ok=True)

    # Check existing subtitles
    conn = get_db(db_path)
    existing_en = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT chapter_index FROM chapter_subtitles "
            "WHERE audiobook_id = ? AND locale = 'en'",
            (book_id,),
        ).fetchall()
    }
    conn.close()

    if existing_en:
        logger.info("  Book %d: %d English chapters already transcribed", book_id, len(existing_en))

    stt = get_stt_provider("", workload=WorkloadHint.LONG_FORM)
    logger.info("  STT provider: %s", stt.name)

    gen_conn = sqlite3.connect(db_path)
    gen_conn.execute("PRAGMA journal_mode=WAL")
    gen_conn.execute("PRAGMA foreign_keys=ON")

    try:

        def on_progress(ch_idx: int, total: int, title: str):
            logger.info("  Chapter %d/%d: %s", ch_idx + 1, total, title)
            try:
                gen_conn.execute(
                    "UPDATE translation_queue "
                    "SET last_progress_at = CURRENT_TIMESTAMP, total_chapters = ? "
                    "WHERE audiobook_id = ? AND locale = ? AND state = 'processing'",
                    (total, book_id, locale),
                )
                gen_conn.commit()
            except Exception as _e:
                logger.debug("Progress update failed (non-fatal): %s", _e)

        def on_chapter_complete(ch_idx: int, source_vtt: Path, translated_vtt: Path | None):
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
            logger.info("  Saved subtitles for chapter %d (en + %s)", ch_idx, locale)

        generate_book_subtitles(
            audio_path=audio_path,
            output_dir=subtitle_dir,
            target_locale=locale,
            stt_provider=stt,
            on_progress=on_progress,
            on_chapter_complete=on_chapter_complete,
            skip_chapters=existing_en,
        )
        return True
    finally:
        gen_conn.close()


def process_book_tts(db_path: str, book_id: int, locale: str, audio_path: Path) -> bool:
    """Run TTS narration for a single book. Returns True on success."""
    from localization.config import TTS_VOICE_ZH
    from localization.selection import WorkloadHint
    from localization.tts.factory import get_tts_provider, synthesize_with_fallback

    conn = get_db(db_path)
    vtt_rows = conn.execute(
        "SELECT chapter_index, vtt_path FROM chapter_subtitles "
        "WHERE audiobook_id = ? AND locale = ? ORDER BY chapter_index",
        (book_id, locale),
    ).fetchall()
    conn.close()

    if not vtt_rows:
        logger.warning("  No translated subtitles — skipping TTS")
        return True  # Not a fatal error

    tts = get_tts_provider(None, workload=WorkloadHint.LONG_FORM)
    voice = TTS_VOICE_ZH
    output_dir = audio_path.parent / "translated"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("  TTS provider: %s, voice: %s, chapters: %d", tts.name, voice, len(vtt_rows))

    for row in vtt_rows:
        ch_idx = row["chapter_index"]
        vtt_path = Path(row["vtt_path"])

        if not vtt_path.exists():
            logger.warning("  VTT missing for chapter %d: %s", ch_idx, vtt_path)
            continue

        vtt_text = vtt_path.read_text(encoding="utf-8")
        lines = []
        for block in vtt_text.split("\n\n"):
            for line in block.strip().split("\n"):
                if (
                    line.strip()
                    and not line.startswith("WEBVTT")
                    and "-->" not in line
                    and not line.strip().isdigit()
                ):
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

        logger.info("  Narrating chapter %d/%d", ch_idx + 1, len(vtt_rows))
        synthesize_with_fallback(tts, full_text, locale, voice, intermediate_path)

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
            logger.warning("  Opus transcode failed: %s", transcode.stderr[:200])
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
        except Exception as _e:
            logger.debug("ffprobe duration probe failed (non-fatal): %s", _e)

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

    return True


def _parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Batch translation processor")
    parser.add_argument("--db", required=True, help="Path to audiobooks.db")
    parser.add_argument("--library", required=True, help="Path to audiobook library")
    parser.add_argument("--book-id", type=int, help="Process a single book ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--stt-only", action="store_true", help="Only run STT, skip TTS")
    parser.add_argument("--tts-only", action="store_true", help="Only run TTS, skip STT")
    return parser.parse_args()


def _configure_env(args):
    """Set environment variables for localization modules from audiobooks.conf."""
    # Set environment for the localization modules
    os.environ.setdefault("AUDIOBOOKS_WHISPER_GPU_HOST", "127.0.0.1")
    os.environ.setdefault("AUDIOBOOKS_WHISPER_GPU_PORT", "8765")

    # Load config from audiobooks.conf
    conf_path = Path("/etc/audiobooks/audiobooks.conf")
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def _get_queue_stats(db_path):
    """Return (pending, completed, failed) counts from the translation queue."""
    conn = get_db(db_path)
    pending = conn.execute(
        "SELECT COUNT(*) FROM translation_queue WHERE state = 'pending'"
    ).fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM translation_queue WHERE state = 'completed'"
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM translation_queue WHERE state = 'failed'"
    ).fetchone()[0]
    conn.close()
    return pending, completed, failed


def _dry_run_preview(db_path):
    """Log the next 20 pending books without processing them."""
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT tq.audiobook_id, a.title, tq.locale, tq.priority "
        "FROM translation_queue tq "
        "JOIN audiobooks a ON a.id = tq.audiobook_id "
        "WHERE tq.state = 'pending' "
        "ORDER BY tq.priority DESC, tq.created_at ASC "
        "LIMIT 20"
    ).fetchall()
    conn.close()
    logger.info("Next %d books to process:", len(rows))
    for r in rows:
        logger.info("  [%d] %s (locale=%s, priority=%d)", r[0], r[1], r[2], r[3])


def _process_job(db_path, job, args, pending, start_time, processed):
    """Validate and process a single translation job.

    Returns the updated pending count, or None if the job was skipped
    (book/file not found — caller should continue to next job).
    """
    book_id = job["audiobook_id"]
    locale = job["locale"]

    conn = get_db(db_path)
    book = conn.execute(
        "SELECT id, title, file_path FROM audiobooks WHERE id = ?", (book_id,)
    ).fetchone()
    conn.close()

    if not book:
        finish_job(db_path, job["id"], "failed", error="Book not found in DB")
        return None

    audio_path = Path(book["file_path"])
    if not audio_path.exists():
        finish_job(db_path, job["id"], "failed", error=f"Audio file not found: {audio_path}")
        return None

    elapsed = time.monotonic() - start_time
    rate = processed / (elapsed / 3600) if elapsed > 0 else 0

    logger.info(
        "=== [%d/%d] Book %d: %s (locale=%s) === [%.1f books/hr]",
        processed,
        processed + pending - 1,
        book_id,
        book["title"],
        locale,
        rate,
    )

    try:
        if not args.tts_only:
            logger.info("  Step 1: STT + subtitle translation")
            process_book_stt(db_path, book_id, locale, audio_path)

        if not args.stt_only:
            logger.info("  Step 2: TTS narration")
            process_book_tts(db_path, book_id, locale, audio_path)

        finish_job(db_path, job["id"], "completed")
        logger.info("  DONE: %s", book["title"])

    except Exception as e:
        logger.exception("  FAILED: %s — %s", book["title"], e)
        finish_job(db_path, job["id"], "failed", error=str(e))

    # Update pending count after processing
    conn = get_db(db_path)
    new_pending = conn.execute(
        "SELECT COUNT(*) FROM translation_queue WHERE state = 'pending'"
    ).fetchone()[0]
    conn.close()
    return new_pending


def _run_batch(db_path, args):
    """Drain the translation queue, processing one job at a time."""
    processed = 0
    pending = 0
    start_time = time.monotonic()

    while not _shutdown:
        job = next_pending_job(db_path, book_id=args.book_id)
        if not job:
            if args.book_id:
                logger.info("Book %d: no pending jobs", args.book_id)
            else:
                logger.info("Queue empty — all jobs processed")
            break

        processed += 1
        new_pending = _process_job(db_path, job, args, pending, start_time, processed)
        if new_pending is None:
            # Job was skipped (book/file not found); don't count it
            processed -= 1
        else:
            pending = new_pending

    total_elapsed = time.monotonic() - start_time
    logger.info(
        "Batch complete: %d books processed in %.1f minutes (%.1f books/hr)",
        processed,
        total_elapsed / 60,
        processed / (total_elapsed / 3600) if total_elapsed > 0 else 0,
    )


def main():
    # Must run as audiobooks service account — DB + config are 0640
    # audiobooks:audiobooks. Fails fast with a usage diagnostic if not.
    from config import require_audiobooks_user  # noqa: E402

    require_audiobooks_user()

    args = _parse_args()
    db_path = args.db

    if not Path(db_path).exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)

    _configure_env(args)
    ensure_tables(db_path)

    pending, completed, failed = _get_queue_stats(db_path)
    logger.info("Queue: %d pending, %d completed, %d failed", pending, completed, failed)

    if args.dry_run:
        _dry_run_preview(db_path)
        return

    _run_batch(db_path, args)


if __name__ == "__main__":
    main()
