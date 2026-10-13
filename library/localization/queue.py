"""Translation queue — persistence and enqueueing only.

Rows are added by:
- book scan/import (every configured non-English locale, post-insert hook)
- user locale activation (books missing that locale's assets)
- a player bump (raises one book's priority)

Nothing in the API process EXECUTES queue work (Audiobook-Manager-4j4). Both
stages are done out of process by the scheduled drain
(``scripts/translation-drain.sh`` → ``scripts/batch-translate.py``):
subtitles (STT + MT) on a rented GPU node under a spend cap, narration (TTS)
on the host afterwards. The in-process worker this module used to run was
removed: a player bump started it, it ran whole-book TTS inside the API
(blocking the gevent worker) or GPU work through whatever provider auto
selection found — paid ones included — outside every spend control.

The schema has one definition, :mod:`localization.queue_schema`.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_db_path: Path | None = None
_library_path: Path | None = None


def init_queue(database_path: Path, library_path: Path) -> None:
    global _db_path, _library_path
    _db_path = database_path
    _library_path = library_path
    _ensure_queue_table()
    _recover_stale_jobs()


def _get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the queue database.

    ``db_path`` lets a caller outside the API process (the importer's
    post-insert hook) name the database explicitly; without it the module
    global set by :func:`init_queue` is used.
    """
    if db_path is not None:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
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


def _ensure_queue_table(db_path: Path | None = None) -> None:
    """Create/upgrade the queue table (single definition: ``queue_schema``)."""
    from .queue_schema import ensure_queue_schema

    conn = _get_db(db_path)
    try:
        remapped = ensure_queue_schema(conn)
        if remapped:
            logger.info(
                "translation_queue: %d completed row(s) without narration reopened "
                "for the narration stage (two-stage accounting)",
                remapped,
            )
    finally:
        conn.close()


def _recover_stale_jobs() -> None:
    """Reset 'processing' rows left by an API worker of an earlier release.

    The API no longer claims rows; this only returns rows a pre-4j4 API worker
    (``claimed_by`` NULL or ``api:<pid>``) left behind. Rows the drain holds
    are never touched.
    """
    conn = _get_db()
    try:
        # Only legacy API rows. Rows the scheduled drain holds
        # (claimed_by='drain') belong to a process that may still be running;
        # the drain releases its own.
        updated = conn.execute(
            "UPDATE translation_queue SET state = 'pending', started_at = NULL, claimed_by = NULL "
            "WHERE state = 'processing' AND (claimed_by IS NULL OR claimed_by LIKE 'api:%')"
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

    ``start_worker`` is accepted for API compatibility and means only "a user
    is waiting for this book": the row's priority is raised so the next drain
    does it first. Nothing is processed in this process.
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
        bump_priority(audiobook_id, locale, priority=max(priority, 100))


def target_locales() -> list[str]:
    """Return the non-English locales a new book should be translated into.

    Reads ``SUPPORTED_LOCALES`` at call time, strips whitespace, drops empty
    elements and English (``en`` or any ``en-*`` tag — the source language),
    de-duplicates, and refuses anything that fails
    ``common_utils.locale_safety.is_safe_locale`` (logged, never queued) — a
    queued locale flows into file names, SQL keys and provider prompts.
    """
    from common_utils.locale_safety import is_safe_locale

    from .config import SUPPORTED_LOCALES

    out: list[str] = []
    for raw in SUPPORTED_LOCALES:
        loc = raw.strip()
        if not loc:
            continue
        if not is_safe_locale(loc):
            logger.warning("Ignoring malformed locale %r in SUPPORTED_LOCALES", raw)
            continue
        if loc.lower() == "en" or loc.lower().startswith("en-"):
            continue
        if loc not in out:
            out.append(loc)
    return out


def enqueue_book_all_locales(
    audiobook_id: int, priority: int = 0, *, db_path: Path | None = None
) -> int:
    """Queue a book for translation in all configured non-English locales.

    Idempotent: ``INSERT OR IGNORE`` against ``UNIQUE(audiobook_id, locale)``.
    Returns the number of rows actually inserted.

    ``db_path`` is for callers outside the API process — the importer's
    post-insert hook runs in the mover/scanner process, where
    :func:`init_queue` is never called. With ``db_path`` the table is
    created if absent (a fresh install whose API has not started yet), so
    the first book ever imported is queued too.
    """
    locales = target_locales()
    if not locales:
        return 0
    if db_path is not None:
        _ensure_queue_table(db_path)
    conn = _get_db(db_path)
    try:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO translation_queue "
            "(audiobook_id, locale, priority) VALUES (?, ?, ?)",
            [(audiobook_id, loc, priority) for loc in locales],
        )
        conn.commit()
        return conn.total_changes - before
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
            "quarantined": counts.get("quarantined", 0),
            # Kept for response-shape compatibility: nothing runs in-process.
            "current": None,
        }
    finally:
        conn.close()


def get_book_translation_status(audiobook_id: int, locale: str) -> dict | None:
    """Return translation status for a specific book+locale.

    Streaming pipeline supersedes legacy pretranslation: if the book has
    streaming_segments rows for this locale, return state="streaming" so
    the UI defers to the streaming overlay instead of the legacy banner.
    """
    conn = _get_db()
    try:
        # Streaming-first: if the on-demand pipeline has segments for this
        # book+locale, the legacy translation_queue row (if any) is stale.
        seg_row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END) AS done "
            "FROM streaming_segments WHERE audiobook_id = ? AND locale = ?",
            (audiobook_id, locale),
        ).fetchone()
        if seg_row and (seg_row["total"] or 0) > 0:
            total = int(seg_row["total"] or 0)
            done = int(seg_row["done"] or 0)
            return {
                "state": "streaming",
                "phase": "streaming",
                "audiobook_id": audiobook_id,
                "locale": locale,
                "total": total,
                "completed": done,
            }

        row = conn.execute(
            "SELECT * FROM translation_queue WHERE audiobook_id = ? AND locale = ?",
            (audiobook_id, locale),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        # Legacy translation_queue is superseded by the streaming pipeline
        # for all non-English locales. Pending / processing / failed rows
        # from the pre-streaming batch era still sit in the DB (no worker
        # drains them), so surfacing them as current status reports stale
        # state to the UI — most visibly "字幕生成失败" toasts on every
        # first-open of any untranslated book, originating from a single
        # 2026-04-19 "No STT provider configured" crash that marked 1,844
        # rows failed. Collapse all three to a `deferred` signal; the
        # frontend hides the legacy banner and defers to the streaming
        # overlay. Completed rows still pass through — they mean VTT files
        # exist on disk from a successful legacy run and are legitimate.
        if locale != "en" and result["state"] in ("failed", "pending", "processing", "quarantined"):
            return {
                "state": "deferred",
                "reason": "streaming_pipeline",
                "audiobook_id": audiobook_id,
                "locale": locale,
            }
        return result
    finally:
        conn.close()
