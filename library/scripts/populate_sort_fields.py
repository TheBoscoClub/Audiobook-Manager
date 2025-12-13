#!/usr/bin/env python3
"""
Populate sort fields for audiobooks database.

Extracts:
- Author/narrator first and last names from full name
- Series sequence numbers from series/title
- Edition information from title
- Acquired date from file modification time
"""

import sqlite3
import sys
import re
import os
from pathlib import Path
from datetime import datetime
from argparse import ArgumentParser

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH


def extract_name_parts(full_name):
    """
    Extract first and last name from a full name.
    Handles various formats:
    - "John Smith" -> ("John", "Smith")
    - "J.R.R. Tolkien" -> ("J.R.R.", "Tolkien")
    - "Stephen King" -> ("Stephen", "King")
    - "John le Carré" -> ("John", "le Carré")
    - "P. G. Wodehouse" -> ("P. G.", "Wodehouse")
    - "Arthur Conan Doyle" -> ("Arthur Conan", "Doyle")
    - "Nelson Mandela (editor)" -> ("Nelson", "Mandela")
    - Multiple authors: "John Smith, Jane Doe" -> use first author
    """
    if not full_name or full_name.lower() in ['unknown author', 'unknown narrator', 'audiobook']:
        return (None, None)

    # Remove role suffixes like "(editor)", "(translator)", "(contributor)", etc.
    import re
    full_name = re.sub(r'\s*\([^)]*\)\s*$', '', full_name).strip()

    # Handle multiple authors - use first one
    if ',' in full_name and ' - ' not in full_name:
        # Check if it's actually multiple authors vs "Last, First" format
        parts = full_name.split(',')
        if len(parts) == 2 and len(parts[0].split()) == 1 and len(parts[1].strip().split()) == 1:
            # Likely "Last, First" format
            return (parts[1].strip(), parts[0].strip())
        else:
            # Multiple authors - use first
            full_name = parts[0].strip()
            # Remove role suffix from first author too
            full_name = re.sub(r'\s*\([^)]*\)\s*$', '', full_name).strip()

    # Handle "Author - role" format (e.g., "Stephen Fry - introductions")
    if ' - ' in full_name:
        full_name = full_name.split(' - ')[0].strip()

    # Split into words
    words = full_name.split()

    if len(words) == 0:
        return (None, None)
    elif len(words) == 1:
        return (None, words[0])  # Only last name
    else:
        # Last word is usually the last name
        # But handle prefixes like "le", "de", "van", "von"
        last_name_prefixes = ['le', 'de', 'la', 'van', 'von', 'der', 'den', 'del', 'da']

        last_name_parts = [words[-1]]
        first_name_parts = words[:-1]

        # Check if second-to-last word is a prefix
        if len(words) > 2 and words[-2].lower() in last_name_prefixes:
            last_name_parts.insert(0, words[-2])
            first_name_parts = words[:-2]

        first_name = ' '.join(first_name_parts) if first_name_parts else None
        last_name = ' '.join(last_name_parts)

        return (first_name, last_name)


def extract_series_sequence(series, title):
    """
    Extract series sequence number from series field or title.

    Patterns:
    - "Book 1", "Book 2", etc.
    - "#1", "#2", etc.
    - "Part 1", "Part 2", etc.
    - "Volume 1", "Vol. 1", etc.
    - "Books 1-3" -> 1 (first in range)
    - Roman numerals: "Book I", "Book II", etc.
    """
    text = f"{series or ''} {title or ''}".lower()

    # Patterns to extract sequence numbers
    patterns = [
        r'book\s*(\d+(?:\.\d+)?)',
        r'#\s*(\d+(?:\.\d+)?)',
        r'part\s*(\d+(?:\.\d+)?)',
        r'vol(?:ume)?\.?\s*(\d+(?:\.\d+)?)',
        r'season\s*(\d+(?:\.\d+)?)',
        r'episode\s*(\d+(?:\.\d+)?)',
        r'books?\s*(\d+)\s*[-–]\s*\d+',  # "Books 1-3" -> 1
        r',\s*book\s*(\d+)',
        r';\s*book\s*(\d+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))

    # Try Roman numerals
    roman_patterns = [
        (r'book\s+(i{1,3}|iv|v|vi{0,3}|ix|x)(?:\s|$|:)', {
            'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
            'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10
        })
    ]

    for pattern, numerals in roman_patterns:
        match = re.search(pattern, text)
        if match:
            roman = match.group(1).lower()
            if roman in numerals:
                return float(numerals[roman])

    return None


def extract_edition(title):
    """
    Extract edition information from title.

    Patterns:
    - "20th Anniversary Edition"
    - "Unabridged"
    - "Complete Edition"
    - "Revised Edition"
    - "2nd Edition"
    - "Collector's Edition"
    """
    if not title:
        return None

    patterns = [
        r'(\d+(?:st|nd|rd|th)\s+anniversary(?:\s+edition)?)',
        r'(\d+(?:st|nd|rd|th)\s+edition)',
        r'(anniversary\s+edition)',
        r'(collector\'?s?\s+edition)',
        r'(complete\s+(?:and\s+)?(?:unabridged\s+)?edition)',
        r'(revised\s+(?:and\s+)?(?:expanded\s+)?edition)',
        r'(definitive\s+(?:collection|edition))',
        r'(unabridged)',
        r'(abridged)',
        r'(special\s+edition)',
        r'(deluxe\s+edition)',
        r'(expanded\s+edition)',
        r'(remastered)',
    ]

    title_lower = title.lower()
    for pattern in patterns:
        match = re.search(pattern, title_lower)
        if match:
            return match.group(1).title()

    return None


def get_file_acquired_date(file_path):
    """Get file modification time as acquired date."""
    try:
        path = Path(file_path)
        if path.exists():
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
    except Exception:
        pass
    return None


def populate_sort_fields(dry_run=True):
    """Populate sort fields for all audiobooks."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("=" * 70)
    print("POPULATE SORT FIELDS")
    print("=" * 70)
    print(f"Database: {DB_PATH}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print()

    # Get all audiobooks
    cursor.execute("""
        SELECT id, title, author, narrator, series, file_path,
               author_last_name, author_first_name,
               narrator_last_name, narrator_first_name,
               series_sequence, edition, acquired_date
        FROM audiobooks
        ORDER BY id
    """)
    audiobooks = cursor.fetchall()

    print(f"Processing {len(audiobooks)} audiobooks...")
    print()

    updates = {
        'author_names': 0,
        'narrator_names': 0,
        'series_seq': 0,
        'edition': 0,
        'acquired': 0
    }

    sample_updates = []

    for book in audiobooks:
        book_updates = {}

        # Extract author names if not already set
        if not book['author_last_name']:
            first, last = extract_name_parts(book['author'])
            if last:
                book_updates['author_last_name'] = last
                book_updates['author_first_name'] = first
                updates['author_names'] += 1

        # Extract narrator names if not already set
        if not book['narrator_last_name']:
            first, last = extract_name_parts(book['narrator'])
            if last:
                book_updates['narrator_last_name'] = last
                book_updates['narrator_first_name'] = first
                updates['narrator_names'] += 1

        # Extract series sequence if not already set
        if book['series_sequence'] is None:
            seq = extract_series_sequence(book['series'], book['title'])
            if seq:
                book_updates['series_sequence'] = seq
                updates['series_seq'] += 1

        # Extract edition if not already set
        if not book['edition']:
            ed = extract_edition(book['title'])
            if ed:
                book_updates['edition'] = ed
                updates['edition'] += 1

        # Get acquired date if not already set
        if not book['acquired_date']:
            acq = get_file_acquired_date(book['file_path'])
            if acq:
                book_updates['acquired_date'] = acq
                updates['acquired'] += 1

        # Apply updates
        if book_updates and not dry_run:
            set_clauses = ', '.join(f"{k} = ?" for k in book_updates.keys())
            values = list(book_updates.values()) + [book['id']]
            cursor.execute(f"UPDATE audiobooks SET {set_clauses} WHERE id = ?", values)

        # Save samples for display
        if book_updates and len(sample_updates) < 10:
            sample_updates.append({
                'title': book['title'][:50],
                'updates': book_updates
            })

    if not dry_run:
        conn.commit()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Author names extracted: {updates['author_names']}")
    print(f"Narrator names extracted: {updates['narrator_names']}")
    print(f"Series sequences found: {updates['series_seq']}")
    print(f"Editions detected: {updates['edition']}")
    print(f"Acquired dates set: {updates['acquired']}")
    print()

    if sample_updates:
        print("=" * 70)
        print("SAMPLE UPDATES:")
        print("=" * 70)
        for sample in sample_updates:
            print(f"\n{sample['title']}")
            for k, v in sample['updates'].items():
                print(f"  {k}: {v}")

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")

    conn.close()


def main():
    parser = ArgumentParser(description="Populate sort fields for audiobooks database")
    parser.add_argument('--execute', action='store_true',
                        help='Actually apply changes (default is dry run)')
    args = parser.parse_args()
    populate_sort_fields(dry_run=not args.execute)


if __name__ == "__main__":
    main()
