"""Queue queries for the scheduled translation drain (``scripts/translation-drain.sh``).

The drain is a bash script; these are the questions it asks the database, kept
in Python so they share the queue's schema and exclusion rules and can be
unit-tested:

``count --stage subtitles|narration``
    Pending rows the given stage would work on (two-stage accounting, see
    ``localization.queue_schema``). ``quarantined`` rows are not pending, so a
    book that keeps failing never makes the drain rent a node on its own. Rows for permanently excluded books are not
    counted: ``batch-translate.py`` retires them at claim time, and renting a
    GPU node for work that will be retired unprocessed is pure cost. Prints one
    integer.

``release --owner drain``
    Returns every ``processing`` row claimed by ``owner`` to ``pending``. The
    drain calls it at start (it holds the drain lock, so any ``drain`` claim is
    left over from a run that died) and at exit (rows its batch was working on
    when it stopped early). Rows other owners hold — the API worker, an
    operator's hand-run batch — are never touched. Prints the number released.

``release-stale [--max-age-hours 6]``
    Releases claims whose owner is gone: an operator's ``batch:<pid>`` whose
    process no longer runs, or any other non-drain owner with no progress for
    the given hours. The drain calls it at start. Prints the number released.

``suspect --job-id N [--max-suspect 3]``
    Raises a row's suspect counter (and quarantines it at the limit) — the
    drain's verdict that the node's services died under this book while the
    node itself was still up. Prints the row's state.

``requeue-quarantined [--book-id N] [--locale L]``
    Puts ``quarantined`` rows (a book that failed on
    ``AUDIOBOOKS_DRAIN_MAX_ATTEMPTS`` consecutive drain runs) back to
    ``pending`` with a fresh attempt counter — the operator's "try it again"
    after fixing the cause. Prints the number requeued.

Usage: python -m localization.drain_queue --db PATH count --stage subtitles
       python -m localization.drain_queue --db PATH release --owner drain
       python -m localization.drain_queue --db PATH requeue-quarantined [--book-id N]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from localization.queue_schema import ensure_queue_schema

STAGE_FILTERS = {
    "subtitles": "subtitles_done_at IS NULL",
    "narration": "subtitles_done_at IS NOT NULL AND narration_done_at IS NULL",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    # mode=rw: never create a database file at a mistyped path.
    conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _has_audiobooks(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiobooks'"
        ).fetchone()
        is not None
    )


def count_drainable(db_path: Path, stage: str = "subtitles") -> int:
    """Pending rows ``stage`` would process (excluded books not counted)."""
    from localization.exclusions import excluded_ids

    where = STAGE_FILTERS[stage]
    conn = _connect(db_path)
    try:
        if not _has_audiobooks(conn):
            return 0
        ensure_queue_schema(conn)
        excluded = excluded_ids(conn)
        rows = conn.execute(
            f"SELECT audiobook_id FROM translation_queue WHERE state = 'pending' AND ({where})"  # nosec B608 — constant filter
        ).fetchall()
        return sum(1 for (book_id,) in rows if book_id not in excluded)
    finally:
        conn.close()


def release_claims(db_path: Path, owner: str) -> int:
    """Return ``owner``'s processing rows to pending."""
    conn = _connect(db_path)
    try:
        if not _has_audiobooks(conn):
            return 0
        ensure_queue_schema(conn)
        cur = conn.execute(
            "UPDATE translation_queue SET state = 'pending', started_at = NULL, claimed_by = NULL "
            "WHERE state = 'processing' AND claimed_by = ?",
            (owner,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def release_stale(db_path: Path, max_age_hours: float = 6.0) -> int:
    """Release claims whose owner is gone, never the drain's or a live owner's.

    * ``batch:<pid>`` (an operator's hand-run batch): released when that pid
      is no longer running on this host.
    * any other non-drain owner (e.g. ``api:<pid>`` from an older release):
      released once ``last_progress_at`` is older than ``max_age_hours``.
    Rows claimed by ``drain`` are handled by :func:`release_claims`.
    """
    conn = _connect(db_path)
    try:
        if not _has_audiobooks(conn):
            return 0
        ensure_queue_schema(conn)
        rows = conn.execute(
            "SELECT id, claimed_by, last_progress_at, "
            "  last_progress_at < datetime('now', ?) AS old "
            "FROM translation_queue WHERE state = 'processing' "
            "AND (claimed_by IS NULL OR claimed_by != 'drain')",
            (f"-{float(max_age_hours)} hours",),
        ).fetchall()
        stale = []
        for row_id, owner, _progress, old in rows:
            owner = owner or ""
            if owner.startswith("batch:") and owner[6:].isdigit():
                if not _pid_alive(int(owner[6:])):
                    stale.append(row_id)
            elif old:
                stale.append(row_id)
        for row_id in stale:
            conn.execute(
                "UPDATE translation_queue SET state = 'pending', started_at = NULL, "
                "claimed_by = NULL WHERE id = ? AND state = 'processing'",
                (row_id,),
            )
        conn.commit()
        return len(stale)
    finally:
        conn.close()


def mark_suspect(db_path: Path, job_id: int, max_suspect: int, error: str) -> str:
    """Raise a row's suspect counter; quarantine it at ``max_suspect`` (bd 2c9).

    Called by the drain only when the GPU endpoints died while this book was
    in flight AND the node still existed (not destroyed by our own guard,
    reaper or dead-man switch). Returns the row's resulting state.
    """
    conn = _connect(db_path)
    try:
        ensure_queue_schema(conn)
        row = conn.execute(
            "UPDATE translation_queue SET suspect_runs = suspect_runs + 1, "
            "state = CASE WHEN suspect_runs + 1 >= ? THEN 'quarantined' ELSE state END, "
            "error = CASE WHEN suspect_runs + 1 >= ? "
            "  THEN 'suspect: ' || ? || ' on ' || (suspect_runs + 1) || ' consecutive runs' "
            "  ELSE error END "
            "WHERE id = ? AND state IN ('pending', 'processing') RETURNING state",
            (max_suspect, max_suspect, error[:1500], job_id),
        ).fetchone()
        conn.commit()
        return row[0] if row else "missing"
    finally:
        conn.close()


def requeue_quarantined(
    db_path: Path, book_id: int | None = None, locale: str | None = None
) -> int:
    """Return quarantined rows to pending with drain_attempts reset."""
    conn = _connect(db_path)
    try:
        if not _has_audiobooks(conn):
            return 0
        ensure_queue_schema(conn)
        sql = (
            "UPDATE translation_queue SET state = 'pending', drain_attempts = 0, suspect_runs = 0, "
            "error = NULL, finished_at = NULL WHERE state = 'quarantined'"
        )
        params: list = []
        if book_id is not None:
            sql += " AND audiobook_id = ?"
            params.append(book_id)
        if locale is not None:
            sql += " AND locale = ?"
            params.append(locale)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drain_queue", description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    cnt = sub.add_parser("count")
    cnt.add_argument("--stage", choices=sorted(STAGE_FILTERS), default="subtitles")
    rel = sub.add_parser("release")
    rel.add_argument("--owner", required=True)
    sus = sub.add_parser("suspect")
    sus.add_argument("--job-id", type=int, required=True)
    sus.add_argument("--max-suspect", type=int, default=3)
    sus.add_argument("--error", default="GPU endpoints lost while in flight")
    stale = sub.add_parser("release-stale")
    stale.add_argument("--max-age-hours", type=float, default=6.0)
    req = sub.add_parser("requeue-quarantined")
    req.add_argument("--book-id", type=int)
    req.add_argument("--locale")
    args = parser.parse_args(argv)

    if not args.db.is_file():
        print(f"drain_queue: database not found: {args.db}", file=sys.stderr)
        return 2
    if args.cmd == "count":
        print(count_drainable(args.db, args.stage))
    elif args.cmd == "release":
        print(release_claims(args.db, args.owner))
    elif args.cmd == "suspect":
        print(mark_suspect(args.db, args.job_id, args.max_suspect, args.error))
    elif args.cmd == "release-stale":
        print(release_stale(args.db, args.max_age_hours))
    else:
        print(requeue_quarantined(args.db, args.book_id, args.locale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
