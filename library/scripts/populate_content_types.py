#!/usr/bin/env python3
"""
Populate content_type in the audiobooks database from the Audible API.

Queries the Audible library API in batches to get content_type and
content_delivery_type for each item, then matches to database by ASIN.
This identifies podcasts, lectures, performances, etc. so the main library
can filter to audiobooks only while making podcasts available in their own
collection.

Usage:
    python3 populate_content_types.py              # dry run
    python3 populate_content_types.py --execute    # apply changes
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH
PAGE_SIZE = 500

# Known podcast/show publishers
PODCAST_PUBLISHERS = frozenset(
    {"wondery", "movewith", "aaptiv", "higher ground", "panoply", "gimlet", "stitcher", "parcast"}
)

# Title patterns strongly indicating podcast/show episodes
EPISODE_PATTERNS = [
    re.compile(r"\bEp(?:isode)?\.?\s*\d", re.IGNORECASE),
    re.compile(r"\|\s*\d+\s*(?:\(Ad-free\))?$"),
    re.compile(r"\bEncore:\s"),
    re.compile(r"\bFirst Listen\s*\|"),
    re.compile(r"\bSeason\s+\d+"),
    re.compile(r"\bDay\s+\d+:.*Meditation", re.IGNORECASE),
]

# Album/file path patterns
PODCAST_PATH_KEYWORDS = ["podcast", "show", "episode"]


def fetch_audible_library() -> list[dict]:
    """Fetch full Audible library via API with content_type metadata."""
    all_items = []
    page = 1

    while True:
        cmd = [
            "audible",
            "api",
            "1.0/library",
            "-p",
            "response_groups=product_attrs",
            "-p",
            f"num_results={PAGE_SIZE}",
            "-p",
            f"page={page}",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": "/usr/local/bin:/usr/bin:/bin"},
            timeout=60,
        )
        if result.returncode != 0:
            print(f"Error querying Audible API (page {page}): {result.stderr}", file=sys.stderr)
            break

        data = json.loads(result.stdout)
        items = data.get("items", [])
        if not items:
            break

        all_items.extend(items)
        print(f"  Fetched page {page}: {len(items)} items (total: {len(all_items)})")

        if len(items) < PAGE_SIZE:
            break
        page += 1

    return all_items


def fetch_catalog_content_type(asin: str) -> dict | None:
    """Fetch content_type for a single ASIN via the catalog endpoint.

    Used for items not in the library list (e.g., individual podcast episodes).
    """
    cmd = ["audible", "api", f"1.0/catalog/products/{asin}", "-p", "response_groups=product_attrs"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": "/usr/local/bin:/usr/bin:/bin"},
            timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        product = data.get("product", {})
        return {
            "content_type": product.get("content_type"),
            "content_delivery_type": product.get("content_delivery_type"),
        }
    except Exception:
        return None


def _build_asin_lookup(audible_items: list[dict]) -> dict[str, dict]:
    """Build lookup dict from Audible items keyed by ASIN."""
    lookup = {}
    for item in audible_items:
        asin = item.get("asin")
        if asin:
            lookup[asin] = {
                "content_type": item.get("content_type"),
                "content_delivery_type": item.get("content_delivery_type"),
                "title": item.get("title", ""),
            }
    return lookup


def _print_api_distribution(audible_by_asin: dict[str, dict]) -> None:
    """Print content type distribution from API data."""
    ct_dist = Counter(v["content_type"] for v in audible_by_asin.values())
    print("Audible library content_type distribution:")
    for ct, count in ct_dist.most_common():
        print(f"  {ct}: {count}")
    print()


def _match_library_pass(all_books: list, audible_by_asin: dict) -> tuple[list, int, int, list]:
    """Pass 1: Match books against library list.

    Returns (updates, already_correct, no_asin, unmatched).
    """
    updates = []
    already_correct = 0
    no_asin = 0
    unmatched = []

    for book in all_books:
        book_asin = book["asin"]

        if not book_asin:
            no_asin += 1
            continue

        if book_asin not in audible_by_asin:
            unmatched.append(book)
            continue

        api_data = audible_by_asin[book_asin]
        new_ct = api_data["content_type"]
        current_ct = book["content_type"]

        if current_ct == new_ct:
            already_correct += 1
        elif new_ct:
            updates.append(
                {
                    "id": book["id"],
                    "asin": book_asin,
                    "title": book["title"],
                    "old_ct": current_ct,
                    "new_ct": new_ct,
                    "delivery_type": api_data["content_delivery_type"],
                }
            )

    return updates, already_correct, no_asin, unmatched


def _print_pass1_report(updates: list, already_correct: int, no_asin: int, unmatched: list) -> None:
    """Print Pass 1 results."""
    print("=" * 70)
    print("PASS 1 — Library list")
    print("=" * 70)
    print(f"ALREADY CORRECT: {already_correct}")
    print(f"NEED UPDATE:     {len(updates)}")
    print(f"NO ASIN:         {no_asin}")
    print(f"NOT IN LIST:     {len(unmatched)} (will try catalog lookup)")

    if not updates:
        return

    update_dist = Counter(u["new_ct"] for u in updates)
    print("\nUpdates by content_type:")
    for ct, count in update_dist.most_common():
        print(f"  → {ct}: {count}")
    print("\nSample updates:")
    for u in updates[:10]:
        print(f"  {u['title'][:55]:55s}  {u['old_ct']} → {u['new_ct']} ({u['delivery_type']})")
    if len(updates) > 10:
        print(f"  ... and {len(updates) - 10} more")


def _catalog_lookup_pass(unmatched: list) -> tuple[list, int, int]:
    """Pass 2: Catalog lookup for unmatched ASINs.

    Returns (catalog_updates, additional_correct, catalog_failed).
    """
    catalog_updates = []
    catalog_failed = 0
    additional_correct = 0

    print(f"\n{'=' * 70}")
    print(f"PASS 2 — Catalog lookup for {len(unmatched)} unmatched ASINs")
    print("=" * 70)

    for i, book in enumerate(unmatched):
        book_asin = book["asin"]
        current_ct = book["content_type"]

        data = fetch_catalog_content_type(book_asin)
        if data and data["content_type"]:
            new_ct = data["content_type"]
            if current_ct != new_ct:
                catalog_updates.append(
                    {
                        "id": book["id"],
                        "asin": book_asin,
                        "title": book["title"],
                        "old_ct": current_ct,
                        "new_ct": new_ct,
                        "delivery_type": data["content_delivery_type"],
                    }
                )
            else:
                additional_correct += 1
        else:
            catalog_failed += 1

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{len(unmatched)}")

    cat_dist = Counter(u["new_ct"] for u in catalog_updates)
    print(f"\nCatalog results: {len(catalog_updates)} updates, {catalog_failed} failed")
    if catalog_updates:
        print("Updates by content_type:")
        for ct, count in cat_dist.most_common():
            print(f"  → {ct}: {count}")

    return catalog_updates, additional_correct, catalog_failed


def _is_podcast_publisher(author: str) -> bool:
    """Check if author matches a known podcast publisher."""
    return any(pub in author for pub in PODCAST_PUBLISHERS)


def _has_episode_pattern(title: str) -> bool:
    """Check if title matches episode numbering patterns."""
    return any(p.search(title) for p in EPISODE_PATTERNS)


def _has_podcast_keywords(fpath: str, desc: str) -> bool:
    """Check if file path or description contains podcast keywords."""
    return any(kw in fpath or kw in desc for kw in PODCAST_PATH_KEYWORDS)


def _detect_podcast_type(row: dict) -> str | None:
    """Detect if a no-ASIN product is a podcast based on heuristics."""
    duration = row["duration_hours"] or 0
    if duration >= 1.5:
        return None

    author = (row["author"] or "").lower()
    title = row["title"] or ""
    fpath = (row["file_path"] or "").lower()
    desc = (row["description"] or "").lower()

    if _is_podcast_publisher(author):
        return "Podcast"
    if _has_episode_pattern(title):
        return "Podcast"
    if _has_podcast_keywords(fpath, desc):
        return "Podcast"

    return None


def _heuristic_pass(cursor) -> list[dict]:
    """Pass 3: Heuristic detection for no-ASIN entries typed as 'Product'."""
    cursor.execute(
        "SELECT id, title, author, duration_hours, file_path, description"
        " FROM audiobooks"
        " WHERE content_type = 'Product'"
        " AND (asin IS NULL OR asin = '')"
    )
    no_asin_products = cursor.fetchall()

    heuristic_updates = []
    for row in no_asin_products:
        detected_type = _detect_podcast_type(dict(row))
        if detected_type:
            heuristic_updates.append(
                {
                    "id": row["id"],
                    "title": row["title"] or "",
                    "old_ct": "Product",
                    "new_ct": detected_type,
                }
            )

    if heuristic_updates:
        print(f"\n{'=' * 70}")
        print(
            f"PASS 3 — Heuristic detection: {len(heuristic_updates)}"
            f" of {len(no_asin_products)} no-ASIN entries reclassified"
        )
        print("=" * 70)
        for u in heuristic_updates[:10]:
            print(f"  {u['title'][:55]:55s}  {u['old_ct']} → {u['new_ct']}")
        if len(heuristic_updates) > 10:
            print(f"  ... and {len(heuristic_updates) - 10} more")

    return heuristic_updates


def _apply_updates(cursor, conn, all_updates: list, dry_run: bool) -> None:
    """Apply content type updates to the database."""
    if not dry_run and all_updates:
        print(f"\nApplying {len(all_updates)} updates...")
        for u in all_updates:
            cursor.execute(
                "UPDATE audiobooks SET content_type = ? WHERE id = ?", (u["new_ct"], u["id"])
            )
        conn.commit()
        print("Done.")
    elif dry_run:
        print("\nDRY RUN — no changes made. Run with --execute to apply.")


def populate_content_types(dry_run: bool = True) -> None:
    """Populate content_type from Audible API."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    print("Fetching library from Audible API...")
    audible_items = fetch_audible_library()
    print(f"Fetched {len(audible_items)} items from Audible API\n")

    if not audible_items:
        print("No items returned from API. Check audible-cli auth.")
        sys.exit(1)

    audible_by_asin = _build_asin_lookup(audible_items)
    _print_api_distribution(audible_by_asin)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, asin, title, content_type FROM audiobooks")
    all_books = cursor.fetchall()
    print(f"Database audiobooks: {len(all_books)}\n")

    # Pass 1: Match against library list
    updates, already_correct, no_asin, unmatched = _match_library_pass(all_books, audible_by_asin)
    _print_pass1_report(updates, already_correct, no_asin, unmatched)

    # Pass 2: Catalog lookup for unmatched ASINs
    catalog_updates: list[tuple[str, int]] = []
    catalog_failed = 0
    if unmatched:
        catalog_updates, additional_correct, catalog_failed = _catalog_lookup_pass(unmatched)
        already_correct += additional_correct

    # Pass 3: Heuristic detection
    heuristic_updates = _heuristic_pass(cursor)

    # Combined results
    all_updates = updates + catalog_updates + heuristic_updates
    print(f"\n{'=' * 70}")
    print(f"TOTAL UPDATES: {len(all_updates)}")
    print(f"ALREADY CORRECT: {already_correct}")
    print(f"NO ASIN: {no_asin}")
    print(f"CATALOG FAILED: {catalog_failed}")
    print(f"HEURISTIC RECLASSIFIED: {len(heuristic_updates)}")
    print("=" * 70)

    _apply_updates(cursor, conn, all_updates, dry_run)
    conn.close()


def main():
    parser = ArgumentParser(description="Populate content_type from Audible API")
    parser.add_argument(
        "--execute", action="store_true", help="Actually apply changes (default is dry run)"
    )
    args = parser.parse_args()
    populate_content_types(dry_run=not args.execute)


if __name__ == "__main__":
    main()
