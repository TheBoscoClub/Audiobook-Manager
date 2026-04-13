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
import subprocess
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


def _signal_handler(sig, frame):
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
    conn = get_db(db_path)
    try:
        if book_id:
            row = conn.execute(
                "SELECT * FROM translation_queue "
                "WHERE audiobook_id = ? AND state = 'pending' LIMIT 1",
                (book_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM translation_queue "
                "WHERE state = 'pending' "
                "ORDER BY priority DESC, created_at ASC LIMIT 1",
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


def finish_job(db_path: str, job_id: int, state: str, error: str | None = None) -> None:
    conn = get_db(db_path)
    try:
        conn.execute(
            "UPDATE translation_queue SET state = ?, error = ?, finished_at = ? "
            "WHERE id = ?",
            (state, error, time.strftime("%Y-%m-%d %H:%M:%S"), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def process_book_stt(
    db_path: str, book_id: int, locale: str, audio_path: Path
) -> bool:
    """Run STT + subtitle translation for a single book. Returns True on success."""
    from localization.chapters import extract_chapters, split_chapter
    from localization.config import DEEPL_API_KEY
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

        transcode = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(intermediate_path),
                "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                str(output_path),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if transcode.returncode == 0:
            intermediate_path.unlink(missing_ok=True)
        else:
            logger.warning("  Opus transcode failed: %s", transcode.stderr[:200])
            output_path = intermediate_path

        duration = None
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(output_path),
                ],
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

    return True


def main():
    parser = argparse.ArgumentParser(description="Batch translation processor")
    parser.add_argument("--db", required=True, help="Path to audiobooks.db")
    parser.add_argument("--library", required=True, help="Path to audiobook library")
    parser.add_argument("--book-id", type=int, help="Process a single book ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--stt-only", action="store_true", help="Only run STT, skip TTS")
    parser.add_argument("--tts-only", action="store_true", help="Only run TTS, skip STT")
    parser.add_argument("--vastai-host", help="Vast.ai Whisper host:port (e.g., 127.0.0.1:8100)")
    args = parser.parse_args()

    db_path = args.db
    library_path = Path(args.library)

    if not Path(db_path).exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)

    # Set environment for the localization modules
    os.environ.setdefault("AUDIOBOOKS_WHISPER_GPU_HOST", "127.0.0.1")
    os.environ.setdefault("AUDIOBOOKS_WHISPER_GPU_PORT", "8765")

    # Vast.ai GPU Whisper via SSH tunnel (preferred for batch workloads)
    if args.vastai_host:
        host, _, port = args.vastai_host.partition(":")
        os.environ["AUDIOBOOKS_VASTAI_WHISPER_HOST"] = host
        os.environ["AUDIOBOOKS_VASTAI_WHISPER_PORT"] = port or "8100"
    else:
        os.environ.setdefault("AUDIOBOOKS_VASTAI_WHISPER_HOST", "127.0.0.1")
        os.environ.setdefault("AUDIOBOOKS_VASTAI_WHISPER_PORT", "8100")

    # Load config from audiobooks.conf
    conf_path = Path("/etc/audiobooks/audiobooks.conf")
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    ensure_tables(db_path)

    # Count pending
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

    logger.info("Queue: %d pending, %d completed, %d failed", pending, completed, failed)

    if args.dry_run:
        conn = get_db(db_path)
        rows = conn.execute(
            "SELECT tq.audiobook_id, a.title, tq.locale, tq.priority "
            "FROM translation_queue tq "
            "JOIN audiobooks a ON a.id = tq.audiobook_id "
            "WHERE tq.state = 'pending' "
            "ORDER BY tq.priority DESC, tq.created_at ASC "
            "LIMIT 20",
        ).fetchall()
        conn.close()
        logger.info("Next %d books to process:", len(rows))
        for r in rows:
            logger.info("  [%d] %s (locale=%s, priority=%d)", r[0], r[1], r[2], r[3])
        return

    processed = 0
    start_time = time.monotonic()

    while not _shutdown:
        job = next_pending_job(db_path, book_id=args.book_id)
        if not job:
            if args.book_id:
                logger.info("Book %d: no pending jobs", args.book_id)
            else:
                logger.info("Queue empty — all jobs processed")
            break

        book_id = job["audiobook_id"]
        locale = job["locale"]

        conn = get_db(db_path)
        book = conn.execute(
            "SELECT id, title, file_path FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        conn.close()

        if not book:
            finish_job(db_path, job["id"], "failed", error="Book not found in DB")
            continue

        audio_path = Path(book["file_path"])
        if not audio_path.exists():
            finish_job(db_path, job["id"], "failed", error=f"Audio file not found: {audio_path}")
            continue

        processed += 1
        elapsed = time.monotonic() - start_time
        rate = processed / (elapsed / 3600) if elapsed > 0 else 0

        logger.info(
            "=== [%d/%d] Book %d: %s (locale=%s) === [%.1f books/hr]",
            processed, processed + pending - 1, book_id, book["title"], locale, rate,
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

        # Update pending count
        conn = get_db(db_path)
        pending = conn.execute(
            "SELECT COUNT(*) FROM translation_queue WHERE state = 'pending'"
        ).fetchone()[0]
        conn.close()

    total_elapsed = time.monotonic() - start_time
    logger.info(
        "Batch complete: %d books processed in %.1f minutes (%.1f books/hr)",
        processed, total_elapsed / 60, processed / (total_elapsed / 3600) if total_elapsed > 0 else 0,
    )


if __name__ == "__main__":
    main()
