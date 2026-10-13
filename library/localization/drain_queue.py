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
            "UPDATE translation_queue SET state = 'pending', drain_attempts = 0, "
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
    else:
        print(requeue_quarantined(args.db, args.book_id, args.locale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
