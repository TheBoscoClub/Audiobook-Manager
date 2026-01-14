#!/usr/bin/env python3
"""
Update content_type field in audiobooks database from Audible API.

This script fetches the actual content_type (Product, Podcast, Show, etc.)
from Audible's API and updates the database accordingly.

Usage:
    python update_content_types.py --dry-run    # Preview changes
    python update_content_types.py              # Apply changes
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# Add library directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent / "library"))
from config import AUDIOBOOKS_DATABASE

DB_PATH = AUDIOBOOKS_DATABASE


def fetch_library_with_content_type() -> dict[str, str]:
    """Fetch library from Audible API and return ASIN -> content_type mapping."""
    print("📚 Fetching library from Audible API...")

    all_items = {}
    page = 1
    page_size = 50

    while True:
        cmd = [
            "audible", "api", "1.0/library",
            "-p", f"num_results={page_size}",
            "-p", f"page={page}",
            "-p", "response_groups=product_attrs",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR: audible api failed: {result.stderr}")
            break

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse JSON: {e}")
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            asin = item.get("asin")
            content_type = item.get("content_type", "Product")
            if asin:
                all_items[asin] = content_type

        print(f"   Fetched page {page}: {len(items)} items (total: {len(all_items)})")

        if len(items) < page_size:
            break
        page += 1

    print(f"✅ Fetched {len(all_items)} items from Audible library")
    return all_items


def get_current_content_types(db_path: Path) -> dict[str, tuple[int, str, str]]:
    """Get current ASIN -> (id, title, content_type) from database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, asin, title, content_type
        FROM audiobooks
        WHERE asin IS NOT NULL AND asin != ''
    """)

    result = {}
    for row in cursor.fetchall():
        id_, asin, title, content_type = row
        result[asin] = (id_, title, content_type)

    conn.close()
    return result


def update_content_types(db_path: Path, updates: list[tuple[str, str, int]], dry_run: bool = False):
    """Update content_type in database."""
    if dry_run:
        print("\n🔸 DRY RUN - No changes will be made\n")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for content_type, asin, id_ in updates:
        cursor.execute(
            "UPDATE audiobooks SET content_type = ? WHERE id = ?",
            (content_type, id_)
        )

    conn.commit()
    conn.close()
    print(f"\n✅ Updated {len(updates)} audiobooks with correct content_type")


def main():
    parser = argparse.ArgumentParser(description="Update content_type from Audible API")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    # Fetch from Audible
    audible_types = fetch_library_with_content_type()

    if not audible_types:
        print("ERROR: No items fetched from Audible")
        sys.exit(1)

    # Get current database state
    db_items = get_current_content_types(args.db)
    print(f"📖 Found {len(db_items)} audiobooks with ASINs in database")

    # Find items that need updating
    updates = []
    mismatches = {"Product": 0, "Podcast": 0, "Show": 0, "Other": 0}

    for asin, audible_type in audible_types.items():
        if asin in db_items:
            id_, title, current_type = db_items[asin]
            if current_type != audible_type:
                updates.append((audible_type, asin, id_))
                category = audible_type if audible_type in mismatches else "Other"
                mismatches[category] = mismatches.get(category, 0) + 1
                print(f"   {current_type} → {audible_type}: {title[:60]}")

    print(f"\n📊 Content Type Changes:")
    print(f"   Items needing update: {len(updates)}")
    for type_, count in mismatches.items():
        if count > 0:
            print(f"   → {type_}: {count}")

    if not updates:
        print("\n✅ All content_types are already correct!")
        return

    # Apply updates
    update_content_types(args.db, updates, dry_run=args.dry_run)

    if args.dry_run:
        print("\nRun without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
