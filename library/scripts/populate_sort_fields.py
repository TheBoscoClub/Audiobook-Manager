#!/usr/bin/env python3
"""
Populate sort fields for audiobooks database.

Extracts:
- Author/narrator first and last names from full name
- Series sequence numbers from series/title
- Edition information from title
- Acquired date from file modification time
"""

import re
import sqlite3
import sys
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH

# Prefixes that belong to the last name (e.g., "le Carre", "van Gogh")
_LAST_NAME_PREFIXES = {"le", "de", "la", "van", "von", "der", "den", "del", "da"}


def _parse_last_first_format(full_name):
    """Handle 'Last, First' format. Returns (first, last) or None."""
    parts = full_name.split(",")
    if len(parts) == 2 and len(parts[0].split()) == 1 and len(parts[1].strip().split()) == 1:
        return (parts[1].strip(), parts[0].strip())
    return None


def _clean_name_input(full_name):
    """Clean and normalize a full name string. Returns cleaned name or None."""
    if not full_name or full_name.lower() in ["unknown author", "unknown narrator", "audiobook"]:
        return None

    # Remove role suffixes like "(editor)", "(translator)"
    full_name = re.sub(r"\s*\([^)]*\)\s*$", "", full_name).strip()
    return full_name


def _handle_multiple_authors(full_name):
    """Handle comma-separated or dash-separated names. Returns the primary name."""
    if "," in full_name and " - " not in full_name:
        result = _parse_last_first_format(full_name)
        if result:
            return result  # Tuple signals early return

        # Multiple authors - use first
        full_name = full_name.split(",")[0].strip()
        full_name = re.sub(r"\s*\([^)]*\)\s*$", "", full_name).strip()

    if " - " in full_name:
        full_name = full_name.split(" - ")[0].strip()

    return full_name


def _split_first_last(words):
    """Split a list of name words into (first_name, last_name)."""
    if not words:
        return (None, None)
    if len(words) == 1:
        return (None, words[0])

    last_name_parts = [words[-1]]
    first_name_parts = words[:-1]

    if len(words) > 2 and words[-2].lower() in _LAST_NAME_PREFIXES:
        last_name_parts.insert(0, words[-2])
        first_name_parts = words[:-2]

    first_name = " ".join(first_name_parts) if first_name_parts else None
    last_name = " ".join(last_name_parts)
    return (first_name, last_name)


def extract_name_parts(full_name):
    """
    Extract first and last name from a full name.
    Handles various formats:
    - "John Smith" -> ("John", "Smith")
    - "J.R.R. Tolkien" -> ("J.R.R.", "Tolkien")
    - "Stephen King" -> ("Stephen", "King")
    - "John le Carre" -> ("John", "le Carre")
    - "P. G. Wodehouse" -> ("P. G.", "Wodehouse")
    - "Arthur Conan Doyle" -> ("Arthur Conan", "Doyle")
    - "Nelson Mandela (editor)" -> ("Nelson", "Mandela")
    - Multiple authors: "John Smith, Jane Doe" -> use first author
    """
    cleaned = _clean_name_input(full_name)
    if cleaned is None:
        return (None, None)

    result = _handle_multiple_authors(cleaned)
    if isinstance(result, tuple):
        return result  # "Last, First" format was detected

    return _split_first_last(result.split())


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
        r"book\s*(\d+(?:\.\d+)?)",
        r"#\s*(\d+(?:\.\d+)?)",
        r"part\s*(\d+(?:\.\d+)?)",
        r"vol(?:ume)?\.?\s*(\d+(?:\.\d+)?)",
        r"season\s*(\d+(?:\.\d+)?)",
        r"episode\s*(\d+(?:\.\d+)?)",
        r"books?\s*(\d+)\s*[-\u2013]\s*\d+",  # "Books 1-3" -> 1
        r",\s*book\s*(\d+)",
        r";\s*book\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))

    # Try Roman numerals
    roman_numerals = {
        "i": 1,
        "ii": 2,
        "iii": 3,
        "iv": 4,
        "v": 5,
        "vi": 6,
        "vii": 7,
        "viii": 8,
        "ix": 9,
        "x": 10,
    }
    match = re.search(r"book\s+(i{1,3}|iv|v|vi{0,3}|ix|x)(?:\s|$|:)", text)
    if match:
        roman = match.group(1).lower()
        return float(roman_numerals.get(roman, 0)) or None

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
        r"(\d+(?:st|nd|rd|th)\s+anniversary(?:\s+edition)?)",
        r"(\d+(?:st|nd|rd|th)\s+edition)",
        r"(anniversary\s+edition)",
        r"(collector\'?s?\s+edition)",
        r"(complete\s+(?:and\s+)?(?:unabridged\s+)?edition)",
        r"(revised\s+(?:and\s+)?(?:expanded\s+)?edition)",
        r"(definitive\s+(?:collection|edition))",
        r"(unabridged)",
        r"(abridged)",
        r"(special\s+edition)",
        r"(deluxe\s+edition)",
        r"(expanded\s+edition)",
        r"(remastered)",
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
            return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        pass  # Non-critical: return None if file stat fails
    return None


def _try_extract_name(book, role):
    """Extract name parts for a role ('author' or 'narrator') if not already set.

    Returns a dict of column updates (may be empty).
    """
    last_col = f"{role}_last_name"
    first_col = f"{role}_first_name"
    if book[last_col]:
        return {}
    first, last = extract_name_parts(book[role])
    if not last:
        return {}
    return {last_col: last, first_col: first}


def _compute_book_updates(book):
    """Compute field updates for a single audiobook row.

    Returns a dict of column->value pairs to update (may be empty).
    """
    updates = {}
    updates.update(_try_extract_name(book, "author"))
    updates.update(_try_extract_name(book, "narrator"))

    if book["series_sequence"] is None:
        seq = extract_series_sequence(book["series"], book["title"])
        if seq:
            updates["series_sequence"] = seq

    if not book["edition"]:
        ed = extract_edition(book["title"])
        if ed:
            updates["edition"] = ed

    if not book["acquired_date"]:
        acq = get_file_acquired_date(book["file_path"])
        if acq:
            updates["acquired_date"] = acq

    return updates


def _count_update_categories(book_updates):
    """Count which categories a book_updates dict touches."""
    counts = {
        "author_names": 1 if "author_last_name" in book_updates else 0,
        "narrator_names": 1 if "narrator_last_name" in book_updates else 0,
        "series_seq": 1 if "series_sequence" in book_updates else 0,
        "edition": 1 if "edition" in book_updates else 0,
        "acquired": 1 if "acquired_date" in book_updates else 0,
    }
    return counts


def _apply_book_update(cursor, book_id, book_updates):
    """Execute a single UPDATE for one audiobook's sort fields."""
    set_clauses = ", ".join(f"{k} = ?" for k in book_updates.keys())
    values = list(book_updates.values()) + [book_id]
    cursor.execute(f"UPDATE audiobooks SET {set_clauses} WHERE id = ?", values)  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query


def _print_sort_summary(totals, sample_updates, dry_run):
    """Print the populate_sort_fields summary."""
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Author names extracted: {totals['author_names']}")
    print(f"Narrator names extracted: {totals['narrator_names']}")
    print(f"Series sequences found: {totals['series_seq']}")
    print(f"Editions detected: {totals['edition']}")
    print(f"Acquired dates set: {totals['acquired']}")
    print()

    if sample_updates:
        print("=" * 70)
        print("SAMPLE UPDATES:")
        print("=" * 70)
        for sample in sample_updates:
            print(f"\n{sample['title']}")
            for k, v in sample["updates"].items():
                print(f"  {k}: {v}")

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")


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

    totals = {"author_names": 0, "narrator_names": 0, "series_seq": 0, "edition": 0, "acquired": 0}
    sample_updates = []

    for book in audiobooks:
        book_updates = _compute_book_updates(book)
        if not book_updates:
            continue

        # Accumulate category counts
        for key, count in _count_update_categories(book_updates).items():
            totals[key] += count

        if not dry_run:
            _apply_book_update(cursor, book["id"], book_updates)

        if len(sample_updates) < 10:
            sample_updates.append({"title": book["title"][:50], "updates": book_updates})

    if not dry_run:
        conn.commit()

    _print_sort_summary(totals, sample_updates, dry_run)
    conn.close()


def main():
    parser = ArgumentParser(description="Populate sort fields for audiobooks database")
    parser.add_argument(
        "--execute", action="store_true", help="Actually apply changes (default is dry run)"
    )
    args = parser.parse_args()
    populate_sort_fields(dry_run=not args.execute)


if __name__ == "__main__":
    main()
