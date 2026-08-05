#!/usr/bin/env python3
"""
Original Print-Publication Year (Open Library)
==============================================
Populates ``audiobooks.original_publish_year`` — the year the book was FIRST
published in print (Open Library ``first_publish_year``) — as opposed to
``published_year``, which is Audible-sourced and conflates book-publication
year with audiobook-release year (~7.5% mismatches in prod).

Wiring (Audiobook-Manager-nfx / .claude/rules/upgrade-consistency.md):
- New imports: dispatched automatically via the ``@register_post_insert``
  hook "Original print year" in ``library/scanner/post_insert.py``.
- Existing rows: the ``--backfill`` CLI below is an OPERATOR-RUN one-shot
  (documented standalone exception — no systemd unit/timer). A full-library
  backfill makes ~1880 polite-rate-limited Open Library calls (~20 min) and
  is resumable, so it is run manually after data migration 014 adds the
  column, not from the service graph or the upgrade path.

Lookup strategy per book (all remote calls timeout-bounded + rate-limited by
``OpenLibraryClient``):
1. ISBN edition lookup -> work -> ``first_publish_year``
2. Title/author search -> best doc (exact-title preferred, similarity-gated)
   -> ``first_publish_year``
3. No match -> column stays NULL. Consumers (sort SQL) fall back to
   ``published_year`` via COALESCE — the fallback is applied at query time,
   never baked into the data.

Usage (CLI):
    # Preview what the backfill would do (no API calls, no writes)
    python3 original_print_year.py --backfill

    # Run the backfill (resumable — skips rows already populated)
    python3 original_print_year.py --backfill --execute

    # Single book by ID
    python3 original_print_year.py --id 42 --execute

Usage (import — post-insert hook):
    from scripts.original_print_year import populate_original_publish_year
    populate_original_publish_year(book_id, db_path)
"""

import sqlite3
import sys
import time
from argparse import ArgumentParser
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
# Make `utils.` imports work when run as a standalone script too
sys.path.insert(0, str(Path(__file__).parent))
from utils.openlibrary_client import OpenLibraryClient  # noqa: E402

from config import DATABASE_PATH  # noqa: E402

DB_PATH = DATABASE_PATH

# Minimum title similarity for accepting a search-result match (mirrors the
# fuzzy threshold used by populate_from_openlibrary.py).
FUZZY_THRESHOLD = 0.85

# Plausibility window for accepted years. OL data has typos (e.g. year 19);
# anything outside this window is discarded rather than stored.
_MIN_PLAUSIBLE_YEAR = 1000

# Extra politeness pause between per-book lookups during bulk backfill, on
# top of OpenLibraryClient's own ~0.6s/request limiter.
BACKFILL_SLEEP_SECONDS = 0.25


def _plausible_year(year) -> Optional[int]:
    """Return the year as int if plausible, else None."""
    try:
        year = int(year)
    except (TypeError, ValueError):  # fmt: skip
        return None
    if _MIN_PLAUSIBLE_YEAR <= year <= datetime.now(timezone.utc).year + 1:
        return year
    return None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _year_from_isbn(client: OpenLibraryClient, isbn: str) -> Optional[int]:
    """ISBN -> edition -> work -> first_publish_year."""
    edition = client.lookup_isbn(isbn)
    if not edition or not edition.work_id:
        return None
    work = client.get_work(edition.work_id)
    if not work:
        return None
    return _plausible_year(work.first_publish_year)


def _year_from_search(client: OpenLibraryClient, title: str, author: str) -> Optional[int]:
    """Title/author search -> best doc -> first_publish_year.

    Prefers an exact (case-insensitive) title match; otherwise accepts the
    top result only when its title clears FUZZY_THRESHOLD similarity —
    a wrong-book year is worse than no year.
    """
    if not title:
        return None
    docs = client.search(title=title, author=author or None, limit=5)
    if not docs:
        return None

    best = None
    for doc in docs:
        if (doc.get("title", "") or "").strip().lower() == title.strip().lower():
            best = doc
            break
    if best is None:
        candidate = docs[0]
        if _similarity(candidate.get("title", ""), title) >= FUZZY_THRESHOLD:
            best = candidate
    if best is None:
        return None
    return _plausible_year(best.get("first_publish_year"))


def lookup_original_publish_year(
    client: OpenLibraryClient,
    title: str,
    author: str = "",
    isbn: str = "",
) -> Optional[int]:
    """Resolve a book's original print-publication year from Open Library.

    Chain: ISBN edition->work lookup, then title/author search. Returns
    None when OL has no confident match (callers leave the column NULL and
    rely on the query-time COALESCE fallback to published_year).
    """
    if isbn:
        year = _year_from_isbn(client, isbn)
        if year:
            return year
    return _year_from_search(client, title, author)


def populate_original_publish_year(
    book_id: int,
    db_path: Path = DB_PATH,
    client: Optional[OpenLibraryClient] = None,
    quiet: bool = True,
) -> Optional[int]:
    """Look up and store original_publish_year for one book (post-insert hook).

    Idempotent: a book whose column is already populated is skipped. Returns
    the stored year, or None when no confident OL match exists.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        row = conn.execute(
            "SELECT title, author, isbn, original_publish_year FROM audiobooks WHERE id = ?",
            (book_id,),
        ).fetchone()
        if row is None:
            return None
        title, author, isbn, existing = row
        if existing is not None:
            return existing

        client = client or OpenLibraryClient()
        year = lookup_original_publish_year(client, title or "", author or "", isbn or "")
        if year is None:
            if not quiet:
                print(f"  [{book_id}] no Open Library match for {title!r} — leaving NULL")
            return None

        conn.execute(
            "UPDATE audiobooks SET original_publish_year = ? WHERE id = ?", (year, book_id)
        )
        conn.commit()
        if not quiet:
            print(f"  [{book_id}] {title!r} -> original print year {year}")
        return year
    finally:
        conn.close()


def backfill(
    db_path: Path = DB_PATH,
    execute: bool = False,
    limit: Optional[int] = None,
    client: Optional[OpenLibraryClient] = None,
) -> dict:
    """Backfill original_publish_year for every row missing it.

    Resumable by construction: only rows with ``original_publish_year IS
    NULL`` are selected, so an interrupted run picks up where it left off.
    Dry-run (default) makes NO network calls and NO writes — it only reports
    what would be processed.

    Returns a summary dict: {candidates, updated, unmatched, errors}.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            "SELECT id, title, author FROM audiobooks "
            "WHERE original_publish_year IS NULL ORDER BY id"
            + (f" LIMIT {int(limit)}" if limit else "")
        ).fetchall()
    finally:
        conn.close()

    summary = {"candidates": len(rows), "updated": 0, "unmatched": 0, "errors": 0}

    if not execute:
        print(f"DRY RUN: {len(rows)} books need original_publish_year lookup")
        for book_id, title, author in rows[:20]:
            print(f"  would look up [{book_id}] {title} — {author}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        est_minutes = len(rows) * (0.6 + BACKFILL_SLEEP_SECONDS) / 60
        print(f"Estimated runtime with rate limiting: ~{est_minutes:.0f} minutes")
        print("Re-run with --execute to perform the backfill (resumable).")
        return summary

    client = client or OpenLibraryClient()
    for idx, (book_id, title, author) in enumerate(rows, 1):
        try:
            year = populate_original_publish_year(
                book_id, db_path=db_path, client=client, quiet=False
            )
            if year is not None:
                summary["updated"] += 1
            else:
                summary["unmatched"] += 1
        except Exception as e:
            summary["errors"] += 1
            print(f"  [{book_id}] error (non-fatal, continuing): {e}", file=sys.stderr)
        print(f"  progress: {idx}/{len(rows)}", end="\r", flush=True)
        # Polite extra pause on top of the client's own rate limiter.
        time.sleep(BACKFILL_SLEEP_SECONDS)

    print()
    print(
        f"Backfill complete: {summary['updated']} updated, "
        f"{summary['unmatched']} unmatched (stay NULL -> published_year fallback), "
        f"{summary['errors']} errors, {summary['candidates']} candidates"
    )
    return summary


def main():
    parser = ArgumentParser(
        description="Populate audiobooks.original_publish_year from Open Library"
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to audiobooks.db")
    parser.add_argument("--backfill", action="store_true", help="Process all rows missing the year")
    parser.add_argument("--id", type=int, help="Process a single book by database ID")
    parser.add_argument(
        "--execute", action="store_true", help="Apply changes (default is dry-run preview)"
    )
    parser.add_argument("--limit", type=int, help="Cap the number of rows processed (backfill)")
    args = parser.parse_args()

    if args.id is not None:
        if not args.execute:
            print(f"DRY RUN: would look up original print year for book id {args.id}")
            return
        year = populate_original_publish_year(args.id, db_path=args.db, quiet=False)
        print(f"Result: {year if year is not None else 'no match (column stays NULL)'}")
        return

    if args.backfill:
        backfill(db_path=args.db, execute=args.execute, limit=args.limit)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
