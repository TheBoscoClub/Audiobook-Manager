#!/usr/bin/env python3
"""
Update narrator information in the audiobooks database from Audible library export.

This script matches audiobooks by title (fuzzy matching) and updates the narrator field.
"""

import json
import sqlite3
import sys
from argparse import ArgumentParser
from difflib import SequenceMatcher
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import normalize_title
from config import AUDIOBOOKS_DATA, DATABASE_PATH

DB_PATH = DATABASE_PATH
AUDIBLE_EXPORT = AUDIOBOOKS_DATA / "library_metadata.json"


def similarity(a, b):
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _load_audible_library():
    """Load and validate the Audible library export file.

    Returns the parsed JSON list. Exits on error.
    """
    if not AUDIBLE_EXPORT.exists():
        print(f"Error: Audible export not found at {AUDIBLE_EXPORT}")
        print(f"Run: audible library export -f json -o {AUDIBLE_EXPORT}")
        sys.exit(1)

    with open(AUDIBLE_EXPORT) as f:
        return json.load(f)


def _build_audible_lookups(audible_library):
    """Build title and ASIN lookup dictionaries from Audible library.

    Returns (audible_by_title, audible_by_asin).
    """
    by_title = {}
    by_asin = {}
    for item in audible_library:
        narrators = item.get("narrators", "")
        if not narrators:
            continue

        entry = {
            "title": item.get("title", ""),
            "narrators": narrators,
            "authors": item.get("authors", ""),
            "asin": item.get("asin", ""),
        }

        norm_title = normalize_title(entry["title"])
        if norm_title:
            by_title[norm_title] = entry
        if entry["asin"]:
            by_asin[entry["asin"]] = entry

    return by_title, by_asin


def _find_match(book, audible_by_title, audible_by_asin):
    """Find a matching Audible entry for a book.

    Returns (match_dict, match_method) or (None, None).
    """
    book_asin = book["asin"]

    # ASIN match (most reliable)
    if book_asin and book_asin in audible_by_asin:
        return audible_by_asin[book_asin], "ASIN"

    # Exact normalized title match
    norm_title = normalize_title(book["title"])
    if norm_title in audible_by_title:
        return audible_by_title[norm_title], "exact title"

    # Fuzzy title match
    return _fuzzy_match(book["title"], audible_by_title)


def _fuzzy_match(book_title, audible_by_title):
    """Find the best fuzzy title match above threshold.

    Returns (match_dict, method_str) or (None, None).
    """
    best_ratio = 0
    best_match = None
    for _aud_title, aud_data in audible_by_title.items():
        ratio = similarity(book_title, aud_data["title"])
        if ratio > best_ratio and ratio >= 0.85:
            best_ratio = ratio
            best_match = aud_data

    if best_match:
        return best_match, f"fuzzy ({best_ratio:.0%})"
    return None, None


def _match_books(unknown_books, audible_by_title, audible_by_asin):
    """Match unknown-narrator books against Audible lookups.

    Returns (updates_list, no_match_list).
    """
    updates = []
    no_match = []

    for book in unknown_books:
        match, method = _find_match(book, audible_by_title, audible_by_asin)
        if match:
            updates.append(
                {
                    "id": book["id"],
                    "title": book["title"],
                    "narrator": match["narrators"],
                    "method": method,
                    "matched_title": match["title"],
                }
            )
        else:
            no_match.append(book["title"])

    return updates, no_match


def _print_match_results(updates, no_match):
    """Print match and no-match summaries."""
    print("=" * 70)
    print(f"MATCHES FOUND: {len(updates)}")
    print("=" * 70)

    for update in updates[:20]:
        print(f"\n{update['title'][:50]}")
        print(f"  -> Narrator: {update['narrator'][:50]}")
        print(f"     Match: {update['method']}")
    if len(updates) > 20:
        print(f"\n... and {len(updates) - 20} more")

    print()
    print("=" * 70)
    print(f"NO MATCH FOUND: {len(no_match)}")
    print("=" * 70)

    for title in no_match[:10]:
        print(f"  - {title[:60]}")
    if len(no_match) > 10:
        print(f"  ... and {len(no_match) - 10} more")


def _apply_updates(cursor, conn, updates):
    """Apply narrator updates to the database."""
    print()
    print("=" * 70)
    print("APPLYING UPDATES...")
    print("=" * 70)

    for update in updates:
        cursor.execute(
            "UPDATE audiobooks SET narrator = ? WHERE id = ?", (update["narrator"], update["id"])
        )

    conn.commit()
    print(f"Updated {len(updates)} records")


def update_narrators(dry_run=True):
    """Update narrator fields from Audible export."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    audible_library = _load_audible_library()
    print(f"Loaded {len(audible_library)} items from Audible library export")

    audible_by_title, audible_by_asin = _build_audible_lookups(audible_library)
    print(f"Built lookup with {len(audible_by_title)} titles, {len(audible_by_asin)} ASINs")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, asin
        FROM audiobooks
        WHERE narrator = 'Unknown Narrator' OR narrator IS NULL OR narrator = ''
    """)
    unknown_narrator_books = cursor.fetchall()
    print(f"Found {len(unknown_narrator_books)} books with unknown narrator")
    print()

    updates, no_match = _match_books(unknown_narrator_books, audible_by_title, audible_by_asin)

    _print_match_results(updates, no_match)

    if not dry_run and updates:
        _apply_updates(cursor, conn, updates)

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")

    conn.close()


def main():
    parser = ArgumentParser(description="Update narrator info from Audible library export")
    parser.add_argument(
        "--execute", action="store_true", help="Actually apply changes (default is dry run)"
    )
    args = parser.parse_args()
    update_narrators(dry_run=not args.execute)


if __name__ == "__main__":
    main()
