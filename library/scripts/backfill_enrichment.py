#!/usr/bin/env python3
"""Backfill enrichment for existing audiobook library.

Phase 1 — ASIN Recovery (no API calls):
  Scan Sources directory for .voucher files, extract ASINs, match to books.

Phase 2 — Enrichment Chain:
  Run provider chain on all books where audible_enriched_at IS NULL.

Usage:
  python3 backfill_enrichment.py --db /path/to/db --sources /path/to/Sources
  python3 backfill_enrichment.py --db /path/to/db --asin-only
  python3 backfill_enrichment.py --db /path/to/db --dry-run
  python3 backfill_enrichment.py --db /path/to/db --limit 10
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

# Ensure the library directory is on the path for scripts.enrichment imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

_ASIN_RE = re.compile(r"^([B0-9][A-Z0-9]{9})_", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Normalize title for fuzzy matching: lowercase, strip punctuation."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_asin_from_voucher_json(voucher_path: Path) -> str | None:
    """Pull the ASIN out of the voucher JSON's content_license block.

    The voucher JSON has two possible locations depending on the Audible
    API version that generated it, so we probe both.
    """
    try:
        data = json.loads(voucher_path.read_text())
    except (json.JSONDecodeError, OSError):  # fmt: skip
        return None
    content_license = data.get("content_license", {})
    direct = content_license.get("asin")
    if direct:
        return direct
    return content_license.get("content_metadata", {}).get("content_reference", {}).get("asin")


def _voucher_asin_and_title(voucher_path: Path) -> tuple[str | None, str]:
    """Return (asin, normalized_title) for a voucher file.

    ASIN is resolved from the JSON first, falling back to the filename.
    The title is extracted from the filename segment after the ASIN
    prefix, with `-AAX…` suffixes stripped.
    """
    m = _ASIN_RE.match(voucher_path.stem)
    filename_asin = m.group(1).upper() if m else None
    json_asin = _extract_asin_from_voucher_json(voucher_path)
    asin = json_asin or filename_asin

    title_part = voucher_path.stem
    if m:
        title_part = title_part[len(m.group(0)) :]
    title_part = re.sub(r"-AAX.*$", "", title_part)
    return asin, _normalize(title_part.replace("_", " "))


def _match_book_ids_by_title(title_map: dict[str, list[int]], title_normalized: str) -> list[int]:
    """Find book IDs whose normalized title matches exactly or fuzzily."""
    book_ids = title_map.get(title_normalized, [])
    if book_ids:
        return book_ids
    for key, ids in title_map.items():
        if title_normalized in key or key in title_normalized:
            return ids
    return []


def _apply_asin_recovery(
    cursor: sqlite3.Cursor, book_ids: list[int], asin: str, dry_run: bool
) -> int:
    """Write the recovered ASIN to each matched book and return the
    count of rows that would be (or were) affected."""
    applied = 0
    for book_id in book_ids:
        if dry_run:
            logger.info("  [DRY RUN] Would set ASIN=%s for book ID %d", asin, book_id)
        else:
            cursor.execute(
                "UPDATE audiobooks SET asin = ? WHERE id = ? AND (asin IS NULL OR asin = '')",
                (asin, book_id),
            )
        applied += 1
    return applied


def phase1_asin_recovery(db_path: Path, sources_dir: Path, dry_run: bool = False) -> int:
    """Recover ASINs from voucher files and source filenames."""
    if not sources_dir.is_dir():
        logger.warning("Sources directory not found: %s", sources_dir)
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get books missing ASINs
    cursor.execute("SELECT id, title, author FROM audiobooks WHERE asin IS NULL OR asin = ''")
    missing = cursor.fetchall()
    if not missing:
        logger.info("Phase 1: All books already have ASINs")
        conn.close()
        return 0

    logger.info("Phase 1: %d books missing ASINs, scanning Sources...", len(missing))

    title_map: dict[str, list[int]] = {}
    for book in missing:
        key = _normalize(book["title"])
        title_map.setdefault(key, []).append(book["id"])

    recovered = 0
    for voucher_path in sources_dir.glob("*.voucher"):
        asin, title_normalized = _voucher_asin_and_title(voucher_path)
        if not asin:
            continue
        book_ids = _match_book_ids_by_title(title_map, title_normalized)
        recovered += _apply_asin_recovery(cursor, book_ids, asin, dry_run)

    if not dry_run:
        conn.commit()
    conn.close()

    logger.info("Phase 1: Recovered %d ASINs from voucher files", recovered)
    return recovered


def phase2_enrichment(
    db_path: Path,
    sources_dir: Path | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    narrator_backfill: bool = False,
) -> dict:
    """Run enrichment chain on un-enriched books.

    When narrator_backfill is True, re-enriches books that were previously
    enriched but still have "Unknown Narrator" — fills in real narrator data
    from the Audible API.
    """
    # Import here to avoid circular deps at module load
    from scripts.enrichment import enrich_book

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if narrator_backfill:
        query = (
            "SELECT id, title, asin FROM audiobooks"
            " WHERE asin IS NOT NULL AND asin != ''"
            " AND (narrator = 'Unknown Narrator' OR narrator IS NULL OR narrator = '')"
        )
    else:
        query = "SELECT id, title, asin FROM audiobooks WHERE audible_enriched_at IS NULL"
    if limit:
        query += f" LIMIT {int(limit)}"
    cursor.execute(query)
    books = cursor.fetchall()
    conn.close()

    if not books:
        logger.info("Phase 2: All books already enriched")
        return {"total": 0, "enriched": 0, "errors": 0, "skipped": 0}

    logger.info("Phase 2: %d books to enrich", len(books))
    stats = {"total": len(books), "enriched": 0, "errors": 0, "skipped": 0}

    for i, book in enumerate(books, 1):
        if dry_run:
            logger.info(
                "  [DRY RUN] Would enrich: %s (ID %d, ASIN=%s)",
                book["title"],
                book["id"],
                book["asin"] or "none",
            )
            continue

        result = enrich_book(
            book_id=book["id"], db_path=db_path, quiet=True, sources_dir=sources_dir
        )

        if result["errors"]:
            stats["errors"] += 1
            logger.warning("  Error enriching %s: %s", book["title"], result["errors"])
        elif result["fields_updated"] > 0:
            stats["enriched"] += 1
        else:
            stats["skipped"] += 1

        if i % 25 == 0:
            logger.info("  Progress: %d/%d enriched", i, len(books))

    logger.info(
        "Phase 2: %d enriched, %d skipped, %d errors (of %d total)",
        stats["enriched"],
        stats["skipped"],
        stats["errors"],
        stats["total"],
    )
    return stats


def phase0_podcast_detection(db_path: Path, dry_run: bool = False) -> int:
    """Reclassify known podcast publishers from 'Product' to 'Podcast'.

    Scans ALL books (not just un-enriched ones) and sets content_type='Podcast'
    for items whose author or publisher matches a known podcast network. This
    catches items without ASINs that the enrichment pipeline can't reach.
    """
    from scripts.enrichment import _PODCAST_PUBLISHERS

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, title, author, publisher FROM audiobooks WHERE content_type = 'Product'"
    )
    candidates = cursor.fetchall()
    reclassified = 0

    for book in candidates:
        author = (book["author"] or "").lower()
        publisher = (book["publisher"] or "").lower()
        combined = author + " " + publisher
        if any(pub in combined for pub in _PODCAST_PUBLISHERS):
            if dry_run:
                logger.info(
                    "  [DRY RUN] Would reclassify as Podcast: %s (ID %d)", book["title"], book["id"]
                )
            else:
                cursor.execute(
                    "UPDATE audiobooks SET content_type = 'Podcast' WHERE id = ?", (book["id"],)
                )
            reclassified += 1

    if not dry_run:
        conn.commit()
    conn.close()

    if reclassified:
        logger.info("Phase 0: Reclassified %d items as Podcast", reclassified)
    else:
        logger.info("Phase 0: No podcast reclassifications needed")
    return reclassified


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill audiobook enrichment")
    parser.add_argument("--db", type=Path, required=True, help="Path to SQLite database")
    parser.add_argument("--sources", type=Path, default=None, help="Path to Sources directory")
    parser.add_argument("--asin-only", action="store_true", help="Phase 1 only (ASIN recovery)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--limit", type=int, default=None, help="Limit Phase 2 to N books")
    parser.add_argument(
        "--narrator-backfill",
        action="store_true",
        help="Re-enrich books with Unknown Narrator to backfill real narrator data",
    )
    args = parser.parse_args()

    if not args.db.exists():
        logger.error("Database not found: %s", args.db)
        return 1

    # Phase 0: Podcast publisher detection (always runs)
    phase0_podcast_detection(args.db, dry_run=args.dry_run)

    # Phase 1: ASIN recovery
    if args.sources:
        phase1_asin_recovery(args.db, args.sources, dry_run=args.dry_run)

    # Phase 2: Enrichment chain
    if not args.asin_only:
        phase2_enrichment(
            args.db,
            sources_dir=args.sources,
            dry_run=args.dry_run,
            limit=args.limit,
            narrator_backfill=args.narrator_backfill,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
