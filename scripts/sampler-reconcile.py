#!/usr/bin/env python3
# /test:wiring-exception: standalone CLI tool, not service-graph wired. Operator runs manually as needed (also exposed via sampler-reconcile wrapper).
"""Sampler reconciler — enqueue 6-min pretranslation for books missing one.

Run this when:
  - A new locale is added to AUDIOBOOKS_SUPPORTED_LOCALES (backfill)
  - A batch of books was imported before the scan-time sampler hook existed
  - You suspect some sampler_jobs rows went missing

Idempotent: only enqueues sampler jobs for (book, locale) pairs that don't
already have a sampler_jobs row. Existing rows (pending/running/complete/failed)
are left alone — admin must explicitly reset failures via the API.

The enqueue inserts segments at priority=2 origin='sampler' — live playback
work (p0/p1) always dominates, so running this reconciler during active use
will NOT delay any listener.

Usage:
  sudo -u audiobooks python3 scripts/sampler-reconcile.py             # all non-EN locales
  sudo -u audiobooks python3 scripts/sampler-reconcile.py --locale zh-Hans
  sudo -u audiobooks python3 scripts/sampler-reconcile.py --max-books 50   # cap
  sudo -u audiobooks python3 scripts/sampler-reconcile.py --dry-run

Exit codes:
  0 — success (even if 0 books needed sampling)
  1 — config / DB error
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

# Add project modules to sys.path so imports work whether this is run from
# /opt/audiobooks/scripts/ (installed) or the project tree.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent / "library", _HERE.parent.parent / "library"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def _connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Foreign keys must be on to get ON DELETE CASCADE semantics.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _supported_non_en_locales() -> list[str]:
    raw = os.environ.get("AUDIOBOOKS_SUPPORTED_LOCALES", "en,zh-Hans")
    return [
        loc.strip()
        for loc in raw.split(",")
        if loc.strip() and not loc.strip().lower().startswith("en")
    ]


def reconcile(
    db_path: str,
    locales: list[str] | None = None,
    max_books: int | None = None,
    dry_run: bool = False,
) -> int:
    """Scan DB, enqueue sampler for missing (book, locale) pairs. Returns
    count of enqueues performed (or would-be-performed in dry-run)."""
    from localization.sampler import enqueue_sampler  # type: ignore

    try:
        from localization.chapters import extract_chapters  # type: ignore
    except ImportError:
        extract_chapters = None  # type: ignore

    targets = locales or _supported_non_en_locales()
    if not targets:
        logging.info("No non-EN locales configured — nothing to reconcile")
        return 0

    conn = _connect_db(db_path)
    books = conn.execute(
        "SELECT id, file_path FROM audiobooks WHERE file_path IS NOT NULL"
    ).fetchall()
    logging.info(
        "Reconciling sampler for %d books × %d locale(s) = %d potential jobs",
        len(books),
        len(targets),
        len(books) * len(targets),
    )

    enqueued = 0
    skipped_existing = 0
    skipped_no_chapters = 0
    failed = 0

    for book in books:
        if max_books is not None and enqueued >= max_books:
            logging.info("Reached --max-books=%d cap; stopping", max_books)
            break
        audiobook_id = book["id"]
        file_path = book["file_path"]

        # Determine which locales need sampling for this book.
        existing_locales = {
            row["locale"]
            for row in conn.execute(
                "SELECT locale FROM sampler_jobs WHERE audiobook_id = ?", (audiobook_id,)
            ).fetchall()
        }
        needed = [loc for loc in targets if loc not in existing_locales]
        if not needed:
            skipped_existing += 1
            continue

        # Pull chapter durations once per book.
        chapter_durations: list[float] = []
        if extract_chapters is not None and file_path:
            try:
                chapters = extract_chapters(Path(file_path))
                chapter_durations = [c.duration_ms / 1000.0 for c in chapters]
            except Exception as e:  # noqa: BLE001
                logging.warning(
                    "extract_chapters failed for book=%d path=%s: %s", audiobook_id, file_path, e
                )

        if not chapter_durations:
            skipped_no_chapters += 1
            logging.debug("book=%d has no chapter metadata — skipping", audiobook_id)
            continue

        for locale in needed:
            if dry_run:
                logging.info(
                    "[DRY RUN] would enqueue sampler: book=%d locale=%s", audiobook_id, locale
                )
                enqueued += 1
                continue
            try:
                result = enqueue_sampler(conn, audiobook_id, locale, chapter_durations)
                status = result.get("status")
                if status in ("running", "pending"):
                    enqueued += 1
                    logging.info(
                        "enqueued: book=%d locale=%s target=%d",
                        audiobook_id,
                        locale,
                        result.get("segments_target", 0),
                    )
                elif status == "skipped":
                    # en* source locale — shouldn't happen here since we filter,
                    # but defensive.
                    pass
                else:
                    logging.warning(
                        "enqueue returned status=%s for book=%d locale=%s reason=%s",
                        status,
                        audiobook_id,
                        locale,
                        result.get("reason"),
                    )
                    failed += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                logging.warning("enqueue failed book=%d locale=%s err=%s", audiobook_id, locale, e)

    conn.close()
    logging.info(
        "Reconcile summary: enqueued=%d skipped_existing=%d skipped_no_chapters=%d failed=%d",
        enqueued,
        skipped_existing,
        skipped_no_chapters,
        failed,
    )
    return enqueued


def main() -> int:
    # Must run as audiobooks service account — DB + config are 0640
    # audiobooks:audiobooks. Fails fast with a usage diagnostic if not.
    from config import require_audiobooks_user  # noqa: E402

    require_audiobooks_user()

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("AUDIOBOOKS_DATABASE", "/var/lib/audiobooks/db/audiobooks.db"),
        help="Path to audiobooks.db (default: from AUDIOBOOKS_DATABASE or /var/lib/audiobooks/db/audiobooks.db)",
    )
    parser.add_argument(
        "--locale",
        action="append",
        dest="locales",
        help="Target locale (repeatable). Defaults to all non-EN from AUDIOBOOKS_SUPPORTED_LOCALES.",
    )
    parser.add_argument("--max-books", type=int, default=None, help="Cap number of books processed")
    parser.add_argument(
        "--dry-run", action="store_true", help="Log what would happen, enqueue nothing"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--burst",
        type=int,
        nargs="?",
        const=4,
        default=None,
        metavar="N",
        help=(
            "After enqueueing, exec sampler-burst.sh to spawn N parallel workers "
            "(default 4 if flag given without a value). Workers exit once the "
            "queue drains. Not compatible with --dry-run."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not Path(args.db).exists():
        logging.error("DB not found: %s", args.db)
        return 1

    reconcile(args.db, args.locales, args.max_books, args.dry_run)

    # --burst: fan out worker processes immediately to drain the queue we
    # just enqueued. No-op on dry-run.
    if args.burst is not None and not args.dry_run:
        burst_script = _HERE / "sampler-burst.sh"
        if not burst_script.is_file():
            logging.warning(
                "--burst requested but sampler-burst.sh not found at %s; "
                "enqueue succeeded, but no burst workers spawned.",
                burst_script,
            )
            return 0
        if not os.access(str(burst_script), os.X_OK):
            logging.warning(
                "--burst requested but %s is not executable; enqueue succeeded, "
                "but no burst workers spawned. Try: chmod +x %s",
                burst_script,
                burst_script,
            )
            return 0
        logging.info("Exec'ing sampler-burst.sh with --workers %d", args.burst)
        # os.execvp replaces this Python process — sampler-burst handles its
        # own signal cleanup, so we don't need to wrap with subprocess.run.
        os.execvp(  # nosec B606 # nosemgrep: dangerous-os-exec-tainted-env-args  — hardcoded sibling script path, int-validated workers
            str(burst_script), [str(burst_script), "--workers", str(args.burst)]
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
