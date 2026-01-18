#!/usr/bin/env python3
"""
One-time migration to backfill ASINs from chapters.json files into audiobooks table.

This is faster than re-scanning the entire library since we only need to read
small JSON files rather than parse audio metadata via ffprobe.
"""

import json
import sqlite3
import sys
from pathlib import Path

# Add parent directories to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import AUDIOBOOKS_LIBRARY, DATABASE_PATH


def extract_asin(chapters_path: Path) -> str | None:
    """Extract ASIN from chapters.json file."""
    try:
        with open(chapters_path) as f:
            data = json.load(f)
        content_metadata = data.get("content_metadata", {})
        content_reference = content_metadata.get("content_reference", {})
        return content_reference.get("asin")
    except (json.JSONDecodeError, IOError):
        return None


def main():
    print(f"Backfilling ASINs into database: {DATABASE_PATH}")
    print(f"Scanning library: {AUDIOBOOKS_LIBRARY}")

    # Find all chapters.json files
    chapters_files = list(AUDIOBOOKS_LIBRARY.rglob("chapters.json"))
    print(f"Found {len(chapters_files)} chapters.json files")

    # Build mapping: directory -> ASIN
    asin_map = {}
    for cf in chapters_files:
        asin = extract_asin(cf)
        if asin:
            asin_map[str(cf.parent)] = asin

    print(f"Extracted {len(asin_map)} ASINs")

    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Get all audiobooks
    cursor.execute(
        "SELECT id, file_path FROM audiobooks WHERE asin IS NULL OR asin = ''"
    )
    audiobooks = cursor.fetchall()
    print(f"Found {len(audiobooks)} audiobooks without ASINs")

    # Update audiobooks with matching ASINs
    updated = 0
    for audiobook_id, file_path in audiobooks:
        # Get directory of audiobook file
        audiobook_dir = str(Path(file_path).parent)
        if audiobook_dir in asin_map:
            cursor.execute(
                "UPDATE audiobooks SET asin = ? WHERE id = ?",
                (asin_map[audiobook_dir], audiobook_id),
            )
            updated += 1

    conn.commit()
    print(f"✓ Updated {updated} audiobooks with ASINs")

    # Sync periodicals is_downloaded status
    cursor.execute("""
        UPDATE periodicals
        SET is_downloaded = 1
        WHERE is_downloaded = 0
        AND asin IN (SELECT asin FROM audiobooks WHERE asin IS NOT NULL AND asin <> '')
    """)
    synced = cursor.rowcount
    if synced > 0:
        conn.commit()
        print(f"✓ Synced is_downloaded for {synced} periodicals")

    # Show final stats
    cursor.execute(
        "SELECT COUNT(*) FROM audiobooks WHERE asin IS NOT NULL AND asin <> ''"
    )
    total_with_asin = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total = cursor.fetchone()[0]
    print(f"\nTotal audiobooks with ASINs: {total_with_asin}/{total}")

    conn.close()


if __name__ == "__main__":
    main()
