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
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_ASIN_RE = re.compile(r"^([B0-9][A-Z0-9]{9})_", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Normalize title for fuzzy matching: lowercase, strip punctuation."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def phase1_asin_recovery(
    db_path: Path, sources_dir: Path, dry_run: bool = False
) -> int:
    """Recover ASINs from voucher files and source filenames."""
    if not sources_dir.is_dir():
        logger.warning("Sources directory not found: %s", sources_dir)
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get books missing ASINs
    cursor.execute(
        "SELECT id, title, author FROM audiobooks WHERE asin IS NULL OR asin = ''"
    )
    missing = cursor.fetchall()
    if not missing:
        logger.info("Phase 1: All books already have ASINs")
        conn.close()
        return 0

    logger.info("Phase 1: %d books missing ASINs, scanning Sources...", len(missing))

    # Build a normalized title → book_id map
    title_map: dict[str, list[int]] = {}
    for book in missing:
        key = _normalize(book["title"])
        title_map.setdefault(key, []).append(book["id"])

    # Scan voucher files
    recovered = 0
    for voucher_path in sources_dir.glob("*.voucher"):
        # Extract ASIN from filename
        m = _ASIN_RE.match(voucher_path.stem)
        filename_asin = m.group(1).upper() if m else None

        # Extract ASIN from voucher JSON
        json_asin = None
        try:
            data = json.loads(voucher_path.read_text())
            json_asin = data.get("content_license", {}).get("asin") or data.get(
                "content_license", {}
            ).get("content_metadata", {}).get("content_reference", {}).get("asin")
        except (json.JSONDecodeError, OSError):
            pass

        asin = json_asin or filename_asin
        if not asin:
            continue

        # Extract title from filename: {ASIN}_{Title}-{format}.voucher
        title_part = voucher_path.stem
        if m:
            title_part = title_part[len(m.group(0)) :]
        title_part = re.sub(r"-AAX.*$", "", title_part)
        title_normalized = _normalize(title_part.replace("_", " "))

        # Match against books
        book_ids = title_map.get(title_normalized, [])
        if not book_ids:
            # Fuzzy: try substring match
            for key, ids in title_map.items():
                if title_normalized in key or key in title_normalized:
                    book_ids = ids
                    break

        for book_id in book_ids:
            if dry_run:
                logger.info(
                    "  [DRY RUN] Would set ASIN=%s for book ID %d", asin, book_id
                )
            else:
                cursor.execute(
                    "UPDATE audiobooks SET asin = ? WHERE id = ? AND (asin IS NULL OR asin = '')",
                    (asin, book_id),
                )
            recovered += 1

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
) -> dict:
    """Run enrichment chain on un-enriched books."""
    # Import here to avoid circular deps at module load
    from scripts.enrichment import enrich_book

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

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
            book_id=book["id"],
            db_path=db_path,
            quiet=True,
            sources_dir=sources_dir,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill audiobook enrichment")
    parser.add_argument(
        "--db", type=Path, required=True, help="Path to SQLite database"
    )
    parser.add_argument(
        "--sources", type=Path, default=None, help="Path to Sources directory"
    )
    parser.add_argument(
        "--asin-only", action="store_true", help="Phase 1 only (ASIN recovery)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit Phase 2 to N books"
    )
    args = parser.parse_args()

    if not args.db.exists():
        logger.error("Database not found: %s", args.db)
        return 1

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
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
