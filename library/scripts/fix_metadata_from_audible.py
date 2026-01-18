#!/usr/bin/env python3
"""
Fix audiobook metadata using Audible library export
Matches titles and updates author/narrator info in the database
"""

import csv
import sqlite3
import sys
from difflib import SequenceMatcher
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import normalize_title
from config import AUDIOBOOKS_DATA, DATABASE_PATH

# Paths - use config or environment
AUDIBLE_TSV = AUDIOBOOKS_DATA / "audible_library.tsv"


def similarity(a, b):
    """Calculate string similarity ratio"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def load_audible_library(tsv_path):
    """Load Audible library from TSV export"""
    library = {}
    with open(tsv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            title = row.get("title", "")
            if title:
                library[normalize_title(title)] = {
                    "title": title,
                    "authors": row.get("authors", ""),
                    "narrators": row.get("narrators", ""),
                    "series_title": row.get("series_title", ""),
                    "series_sequence": row.get("series_sequence", ""),
                    "genres": row.get("genres", ""),
                }
    return library


def find_best_match(db_title, audible_library, threshold=0.7):
    """Find best matching title in Audible library"""
    normalized = normalize_title(db_title)

    # Try exact match first
    if normalized in audible_library:
        return audible_library[normalized]

    # Try fuzzy matching
    best_match = None
    best_score = 0
    for audible_title, data in audible_library.items():
        score = similarity(normalized, audible_title)
        if score > best_score and score >= threshold:
            best_score = score
            best_match = data

    return best_match


def main():
    print("=== Fix Metadata from Audible Library ===")

    # Load Audible library
    print(f"\nLoading Audible library from {AUDIBLE_TSV}...")
    audible_library = load_audible_library(AUDIBLE_TSV)
    print(f"Loaded {len(audible_library)} titles from Audible")

    # Connect to database
    print(f"\nConnecting to database {DATABASE_PATH}...")
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Get all audiobooks
    cursor.execute("SELECT id, title, author, narrator FROM audiobooks")
    audiobooks = cursor.fetchall()
    print(f"Found {len(audiobooks)} audiobooks in database")

    # Track updates
    updated = 0
    not_found = 0
    already_correct = 0

    for ab_id, title, current_author, current_narrator in audiobooks:
        match = find_best_match(title, audible_library)

        if match:
            new_author = match["authors"] or current_author
            new_narrator = match["narrators"] or current_narrator

            # Only update if different
            if new_author != current_author or new_narrator != current_narrator:
                cursor.execute(
                    "UPDATE audiobooks SET author = ?, narrator = ? WHERE id = ?",
                    (new_author, new_narrator, ab_id),
                )
                updated += 1
                if updated <= 10:
                    print(f"  Updated: {title[:50]}")
                    print(f"    Author: {current_author} -> {new_author}")
                    print(f"    Narrator: {current_narrator} -> {new_narrator}")
            else:
                already_correct += 1
        else:
            not_found += 1
            if not_found <= 5:
                print(f"  No match: {title[:60]}")

    # Commit changes
    conn.commit()

    # Show statistics
    print("\n=== Summary ===")
    print(f"Updated: {updated}")
    print(f"Already correct: {already_correct}")
    print(f"No match found: {not_found}")

    # Show new narrator counts
    cursor.execute("SELECT COUNT(DISTINCT narrator) FROM audiobooks")
    narrator_count = cursor.fetchone()[0]
    print(f"\nUnique narrators after update: {narrator_count}")

    # Show top narrators
    cursor.execute(
        """
        SELECT narrator, COUNT(*) as count
        FROM audiobooks
        GROUP BY narrator
        ORDER BY count DESC
        LIMIT 10
    """
    )
    print("\nTop 10 narrators:")
    for narrator, count in cursor.fetchall():
        print(f"  {count:3d} - {narrator[:60]}")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
