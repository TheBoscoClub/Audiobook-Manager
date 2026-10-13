"""Queue queries for the scheduled translation drain (``scripts/translation-drain.sh``).

The drain is a bash script; these are the two questions it needs the database
to answer, kept in Python so they share the queue's exclusion rules and can be
unit-tested:

``count``
    Pending ``translation_queue`` rows that a batch would actually work on —
    rows for permanently excluded books are not counted, because
    ``batch-translate.py`` retires them at claim time and renting a GPU node
    for work that will be retired unprocessed is pure cost. A missing
    ``translation_queue`` table (a fresh install whose API has never started)
    counts as zero. Prints one integer.

``release --since 'YYYY-MM-DD HH:MM:SS'``
    Returns rows the drain's own batch claimed (``state='processing'`` and
    ``started_at >= since``, UTC, the format ``batch-translate.py`` writes) to
    ``pending``. Used when the drain stops a batch early — wall-clock limit,
    SIGTERM, node lost — so the interrupted book is retried at the next drain
    instead of sitting in ``processing`` forever (nothing else resets those
    rows outside an API restart). Rows claimed before the drain started are
    left alone. Prints the number of rows released.

Usage: python -m localization.drain_queue --db PATH count
       python -m localization.drain_queue --db PATH release --since TS
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    # mode=rw: never create a database file at a mistyped path.
    conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _has_queue_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='translation_queue'"
    ).fetchone()
    return row is not None


def count_drainable(db_path: Path) -> int:
    """Pending rows a batch would process (excluded books not counted)."""
    from localization.exclusions import excluded_ids

    conn = _connect(db_path)
    try:
        if not _has_queue_table(conn):
            return 0
        excluded = excluded_ids(conn)
        rows = conn.execute(
            "SELECT audiobook_id FROM translation_queue WHERE state = 'pending'"
        ).fetchall()
        return sum(1 for (book_id,) in rows if book_id not in excluded)
    finally:
        conn.close()


def release_claims(db_path: Path, since: str) -> int:
    """Return rows claimed at or after ``since`` from processing to pending."""
    conn = _connect(db_path)
    try:
        if not _has_queue_table(conn):
            return 0
        cur = conn.execute(
            "UPDATE translation_queue SET state = 'pending', started_at = NULL "
            "WHERE state = 'processing' AND started_at >= ?",
            (since,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drain_queue", description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("count")
    rel = sub.add_parser("release")
    rel.add_argument("--since", required=True)
    args = parser.parse_args(argv)

    if not args.db.is_file():
        print(f"drain_queue: database not found: {args.db}", file=sys.stderr)
        return 2
    if args.cmd == "count":
        print(count_drainable(args.db))
    else:
        print(release_claims(args.db, args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
