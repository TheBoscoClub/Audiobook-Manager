#!/usr/bin/env python3
"""
Populate genre information in the audiobooks database from Audible library export.

This script matches audiobooks by ASIN or title and populates the genres table
and audiobook_genres junction table.
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


def populate_genres(dry_run=True):
    """Populate genres from Audible export."""
    if not AUDIBLE_EXPORT.exists():
        print(f"Error: Audible export not found at {AUDIBLE_EXPORT}")
        print(f"Run: audible library export -f json -o {AUDIBLE_EXPORT}")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    # Load Audible library
    with open(AUDIBLE_EXPORT) as f:
        audible_library = json.load(f)

    print(f"Loaded {len(audible_library)} items from Audible library export")

    # Build lookup by ASIN and title
    audible_by_asin = {}
    audible_by_title = {}

    for item in audible_library:
        asin = item.get("asin", "")
        title = item.get("title", "")
        genres = item.get("genres", "")

        if genres:
            genre_list = [g.strip() for g in genres.split(",") if g.strip()]
            if asin:
                audible_by_asin[asin] = {"title": title, "genres": genre_list}
            if title:
                norm_title = normalize_title(title)
                if norm_title:
                    audible_by_title[norm_title] = {
                        "title": title,
                        "genres": genre_list,
                        "asin": asin,
                    }

    print(
        f"Built lookup with {len(audible_by_asin)} ASINs, {len(audible_by_title)} titles"
    )

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all audiobooks
    cursor.execute("SELECT id, title, asin FROM audiobooks")
    all_books = cursor.fetchall()
    print(f"Found {len(all_books)} audiobooks in database")

    # Track matches and genres
    matches = []
    no_match = []
    all_genres = set()

    for book in all_books:
        book_id = book["id"]
        book_title = book["title"]
        book_asin = book["asin"]

        match = None
        match_method = None

        # Try ASIN match first
        if book_asin and book_asin in audible_by_asin:
            match = audible_by_asin[book_asin]
            match_method = "ASIN"
        else:
            # Try exact normalized title match
            norm_title = normalize_title(book_title)
            if norm_title in audible_by_title:
                match = audible_by_title[norm_title]
                match_method = "exact title"
            else:
                # Try fuzzy title match
                best_ratio = 0
                best_match = None
                for aud_title, aud_data in audible_by_title.items():
                    ratio = similarity(book_title, aud_data["title"])
                    if ratio > best_ratio and ratio >= 0.85:
                        best_ratio = ratio
                        best_match = aud_data

                if best_match:
                    match = best_match
                    match_method = f"fuzzy ({best_ratio:.0%})"

        if match and match["genres"]:
            matches.append(
                {
                    "id": book_id,
                    "title": book_title,
                    "genres": match["genres"],
                    "method": match_method,
                }
            )
            all_genres.update(match["genres"])
        else:
            no_match.append(book_title)

    # Report results
    print()
    print("=" * 70)
    print(f"MATCHES FOUND: {len(matches)}")
    print(f"UNIQUE GENRES: {len(all_genres)}")
    print("=" * 70)

    # Show sample matches
    for m in matches[:10]:
        print(f"\n{m['title'][:50]}")
        print(f"  Genres: {', '.join(m['genres'][:5])}")
        print(f"  Match: {m['method']}")

    if len(matches) > 10:
        print(f"\n... and {len(matches) - 10} more")

    print()
    print("=" * 70)
    print(f"NO MATCH: {len(no_match)}")
    print("=" * 70)

    for title in no_match[:5]:
        print(f"  - {title[:60]}")
    if len(no_match) > 5:
        print(f"  ... and {len(no_match) - 5} more")

    # Apply updates
    if not dry_run and matches:
        print()
        print("=" * 70)
        print("APPLYING UPDATES...")
        print("=" * 70)

        # First, clear existing genre associations
        cursor.execute("DELETE FROM audiobook_genres")
        cursor.execute("DELETE FROM genres")
        print("Cleared existing genre data")

        # Insert all unique genres
        genre_id_map = {}
        for genre in sorted(all_genres):
            cursor.execute("INSERT INTO genres (name) VALUES (?)", (genre,))
            genre_id_map[genre] = cursor.lastrowid
        print(f"Inserted {len(genre_id_map)} genres")

        # Insert audiobook-genre associations (deduplicated)
        association_count = 0
        for m in matches:
            # Deduplicate genres for this book
            seen_genres = set()
            for genre in m["genres"]:
                if genre in genre_id_map and genre not in seen_genres:
                    cursor.execute(
                        "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
                        (m["id"], genre_id_map[genre]),
                    )
                    association_count += 1
                    seen_genres.add(genre)

        conn.commit()
        print(f"Created {association_count} audiobook-genre associations")

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")

    conn.close()

    # Print genre summary
    print()
    print("=" * 70)
    print("TOP GENRES FOUND:")
    print("=" * 70)

    genre_counts = {}
    for m in matches:
        for g in m["genres"]:
            genre_counts[g] = genre_counts.get(g, 0) + 1

    for genre, count in sorted(genre_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {count:4d}  {genre}")


def main():
    parser = ArgumentParser(description="Populate genres from Audible library export")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes (default is dry run)",
    )
    args = parser.parse_args()
    populate_genres(dry_run=not args.execute)


if __name__ == "__main__":
    main()
