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
import subprocess
import sqlite3
import sys
from argparse import ArgumentParser
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH
PAGE_SIZE = 500


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
            print(
                f"Error querying Audible API (page {page}): {result.stderr}",
                file=sys.stderr,
            )
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
    cmd = [
        "audible",
        "api",
        f"1.0/catalog/products/{asin}",
        "-p",
        "response_groups=product_attrs",
    ]
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


def populate_content_types(dry_run: bool = True) -> None:
    """Populate content_type from Audible API."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    # Fetch library from Audible API
    print("Fetching library from Audible API...")
    audible_items = fetch_audible_library()
    print(f"Fetched {len(audible_items)} items from Audible API\n")

    if not audible_items:
        print("No items returned from API. Check audible-cli auth.")
        sys.exit(1)

    # Build lookup by ASIN
    audible_by_asin = {}
    for item in audible_items:
        asin = item.get("asin")
        if asin:
            audible_by_asin[asin] = {
                "content_type": item.get("content_type"),
                "content_delivery_type": item.get("content_delivery_type"),
                "title": item.get("title", ""),
            }

    # Content type distribution from API
    from collections import Counter

    ct_dist = Counter(v["content_type"] for v in audible_by_asin.values())
    print("Audible library content_type distribution:")
    for ct, count in ct_dist.most_common():
        print(f"  {ct}: {count}")
    print()

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, asin, title, content_type FROM audiobooks")
    all_books = cursor.fetchall()
    print(f"Database audiobooks: {len(all_books)}\n")

    # Pass 1: Match against library list (fast, bulk)
    updates = []
    already_correct = 0
    no_asin = 0
    unmatched = []  # ASINs not in library list — need catalog lookup

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

    # Report pass 1
    print("=" * 70)
    print("PASS 1 — Library list")
    print("=" * 70)
    print(f"ALREADY CORRECT: {already_correct}")
    print(f"NEED UPDATE:     {len(updates)}")
    print(f"NO ASIN:         {no_asin}")
    print(f"NOT IN LIST:     {len(unmatched)} (will try catalog lookup)")

    update_dist = Counter(u["new_ct"] for u in updates)
    if updates:
        print("\nUpdates by content_type:")
        for ct, count in update_dist.most_common():
            print(f"  → {ct}: {count}")
        print("\nSample updates:")
        for u in updates[:10]:
            print(
                f"  {u['title'][:55]:55s}  {u['old_ct']} → {u['new_ct']}"
                f" ({u['delivery_type']})"
            )
        if len(updates) > 10:
            print(f"  ... and {len(updates) - 10} more")

    # Pass 2: Catalog lookup for unmatched ASINs (individual API calls)
    catalog_updates = []
    catalog_failed = 0

    if unmatched:
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
                    already_correct += 1
            else:
                catalog_failed += 1

            if (i + 1) % 50 == 0:
                print(f"  Progress: {i + 1}/{len(unmatched)}")

        cat_dist = Counter(u["new_ct"] for u in catalog_updates)
        print(
            f"\nCatalog results: {len(catalog_updates)} updates,"
            f" {catalog_failed} failed"
        )
        if catalog_updates:
            print("Updates by content_type:")
            for ct, count in cat_dist.most_common():
                print(f"  → {ct}: {count}")

    # Pass 3: Heuristic detection for no-ASIN entries still typed as 'Product'.
    # These are podcast/show episodes that Audible classified inconsistently
    # or that were imported without ASIN metadata.
    heuristic_updates = []
    cursor.execute(
        "SELECT id, title, author, duration_hours, file_path, description"
        " FROM audiobooks"
        " WHERE content_type = 'Product'"
        " AND (asin IS NULL OR asin = '')"
    )
    no_asin_products = cursor.fetchall()

    if no_asin_products:
        import re

        # Known podcast/show publishers
        podcast_publishers = frozenset(
            {
                "wondery",
                "movewith",
                "aaptiv",
                "higher ground",
                "panoply",
                "gimlet",
                "stitcher",
                "parcast",
            }
        )
        # Title patterns strongly indicating podcast/show episodes
        episode_patterns = [
            re.compile(r"\bEp(?:isode)?\.?\s*\d", re.IGNORECASE),
            re.compile(r"\|\s*\d+\s*(?:\(Ad-free\))?$"),
            re.compile(r"\bEncore:\s"),
            re.compile(r"\bFirst Listen\s*\|"),
            re.compile(r"\bSeason\s+\d+"),
            re.compile(r"\bDay\s+\d+:.*Meditation", re.IGNORECASE),
        ]
        # Album/file path patterns
        podcast_path_keywords = ["podcast", "show", "episode"]

        for row in no_asin_products:
            title = row["title"] or ""
            author = (row["author"] or "").lower()
            duration = row["duration_hours"] or 0
            fpath = (row["file_path"] or "").lower()
            desc = (row["description"] or "").lower()

            detected_type = None

            # Rule 1: Known podcast publisher + short duration
            if any(pub in author for pub in podcast_publishers) and duration < 1.5:
                detected_type = "Podcast"
            # Rule 2: Episode numbering pattern in title + short duration
            elif duration < 1.5 and any(p.search(title) for p in episode_patterns):
                detected_type = "Podcast"
            # Rule 3: "podcast" in file path or description + short duration
            elif duration < 1.5 and any(
                kw in fpath or kw in desc for kw in podcast_path_keywords
            ):
                detected_type = "Podcast"

            if detected_type:
                heuristic_updates.append(
                    {
                        "id": row["id"],
                        "title": title,
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

    # Combined results
    all_updates = updates + catalog_updates + heuristic_updates
    print(f"\n{'=' * 70}")
    print(f"TOTAL UPDATES: {len(all_updates)}")
    print(f"ALREADY CORRECT: {already_correct}")
    print(f"NO ASIN: {no_asin}")
    print(f"CATALOG FAILED: {catalog_failed}")
    print(f"HEURISTIC RECLASSIFIED: {len(heuristic_updates)}")
    print("=" * 70)

    # Apply
    if not dry_run and all_updates:
        print(f"\nApplying {len(all_updates)} updates...")
        for u in all_updates:
            cursor.execute(
                "UPDATE audiobooks SET content_type = ? WHERE id = ?",
                (u["new_ct"], u["id"]),
            )
        conn.commit()
        print("Done.")
    elif dry_run:
        print("\nDRY RUN — no changes made. Run with --execute to apply.")

    conn.close()


def main():
    parser = ArgumentParser(description="Populate content_type from Audible API")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes (default is dry run)",
    )
    args = parser.parse_args()
    populate_content_types(dry_run=not args.execute)


if __name__ == "__main__":
    main()
