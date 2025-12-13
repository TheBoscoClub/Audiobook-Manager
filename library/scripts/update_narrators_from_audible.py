#!/usr/bin/env python3
"""
Update narrator information in the audiobooks database from Audible library export.

This script matches audiobooks by title (fuzzy matching) and updates the narrator field.
"""

import sqlite3
import json
import sys
import re
from pathlib import Path
from difflib import SequenceMatcher
from argparse import ArgumentParser

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH
AUDIBLE_EXPORT = Path('/raid0/Audiobooks/library_metadata.json')


def normalize_title(title):
    """Normalize title for matching."""
    if not title:
        return ''
    # Remove common suffixes
    title = re.sub(r'\s*\(Unabridged\)\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Unabridged\]\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*:\s*A Novel\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*:\s*A Memoir\s*$', '', title, flags=re.IGNORECASE)
    # Remove punctuation and lowercase
    title = re.sub(r'[^\w\s]', '', title)
    title = ' '.join(title.lower().split())
    return title


def similarity(a, b):
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def update_narrators(dry_run=True):
    """Update narrator fields from Audible export."""
    if not AUDIBLE_EXPORT.exists():
        print(f"Error: Audible export not found at {AUDIBLE_EXPORT}")
        print("Run: audible library export -f json -o /raid0/Audiobooks/library_metadata.json")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    # Load Audible library
    with open(AUDIBLE_EXPORT) as f:
        audible_library = json.load(f)

    print(f"Loaded {len(audible_library)} items from Audible library export")

    # Build lookup by normalized title and by ASIN
    audible_by_title = {}
    audible_by_asin = {}
    for item in audible_library:
        title = item.get('title', '')
        asin = item.get('asin', '')
        narrators = item.get('narrators', '')

        if narrators:
            norm_title = normalize_title(title)
            if norm_title:
                audible_by_title[norm_title] = {
                    'title': title,
                    'narrators': narrators,
                    'authors': item.get('authors', ''),
                    'asin': asin
                }
            if asin:
                audible_by_asin[asin] = {
                    'title': title,
                    'narrators': narrators,
                    'authors': item.get('authors', '')
                }

    print(f"Built lookup with {len(audible_by_title)} titles, {len(audible_by_asin)} ASINs")

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all audiobooks with Unknown Narrator
    cursor.execute("""
        SELECT id, title, author, narrator, asin
        FROM audiobooks
        WHERE narrator = 'Unknown Narrator' OR narrator IS NULL OR narrator = ''
    """)
    unknown_narrator_books = cursor.fetchall()

    print(f"Found {len(unknown_narrator_books)} books with unknown narrator")
    print()

    updates = []
    no_match = []

    for book in unknown_narrator_books:
        book_id = book['id']
        book_title = book['title']
        book_asin = book['asin']

        match = None
        match_method = None

        # Try ASIN match first (most reliable)
        if book_asin and book_asin in audible_by_asin:
            match = audible_by_asin[book_asin]
            match_method = 'ASIN'
        else:
            # Try exact normalized title match
            norm_title = normalize_title(book_title)
            if norm_title in audible_by_title:
                match = audible_by_title[norm_title]
                match_method = 'exact title'
            else:
                # Try fuzzy title match
                best_ratio = 0
                best_match = None
                for aud_title, aud_data in audible_by_title.items():
                    ratio = similarity(book_title, aud_data['title'])
                    if ratio > best_ratio and ratio >= 0.85:  # 85% threshold
                        best_ratio = ratio
                        best_match = aud_data

                if best_match:
                    match = best_match
                    match_method = f'fuzzy ({best_ratio:.0%})'

        if match:
            updates.append({
                'id': book_id,
                'title': book_title,
                'narrator': match['narrators'],
                'method': match_method,
                'matched_title': match['title']
            })
        else:
            no_match.append(book_title)

    # Show results
    print("=" * 70)
    print(f"MATCHES FOUND: {len(updates)}")
    print("=" * 70)

    # Show sample updates
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

    # Apply updates
    if not dry_run and updates:
        print()
        print("=" * 70)
        print("APPLYING UPDATES...")
        print("=" * 70)

        for update in updates:
            cursor.execute(
                "UPDATE audiobooks SET narrator = ? WHERE id = ?",
                (update['narrator'], update['id'])
            )

        conn.commit()
        print(f"Updated {len(updates)} records")

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")

    conn.close()


def main():
    parser = ArgumentParser(description="Update narrator info from Audible library export")
    parser.add_argument('--execute', action='store_true',
                        help='Actually apply changes (default is dry run)')
    args = parser.parse_args()
    update_narrators(dry_run=not args.execute)


if __name__ == "__main__":
    main()
