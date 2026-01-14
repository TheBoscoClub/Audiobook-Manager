#!/usr/bin/env python3
"""
Populate ASINs from source file names.

Audible source files (.aaxc) contain the ASIN in their filename:
  0062868071_The_End_Is_Always_Near_...aaxc
  ^^^^^^^^^^ ASIN

This script:
1. Scans source directory for .aaxc files
2. Extracts ASIN from filename
3. Extracts title from filename
4. Matches to database by normalized title
5. Updates both 'asin' and 'source_asin' columns

This is MORE reliable than API matching since it uses the actual source files.
"""

import argparse
import re
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "library"))
from common import normalize_title
from config import AUDIOBOOKS_DATABASE, AUDIOBOOKS_SOURCES

SOURCES_DIR = AUDIOBOOKS_SOURCES
DB_PATH = AUDIOBOOKS_DATABASE


def extract_asin_and_title(filename: str) -> tuple[str | None, str | None]:
    """Extract ASIN and title from source filename.

    Format: ASIN_Title_With_Underscores-AAX_XX_XXX.aaxc
    Example: 0062868071_The_End_Is_Always_Near_...-AAX_44_128.aaxc

    Returns (asin, title) or (None, None) if parsing fails.
    """
    # Pattern: starts with ASIN (10 alphanumeric), then underscore, then title
    match = re.match(r"^([A-Z0-9]{10})_(.+)-AAX", filename, re.IGNORECASE)
    if not match:
        return None, None

    asin = match.group(1)
    # Convert underscores to spaces for title
    title_part = match.group(2).replace("_", " ")
    return asin, title_part


def get_source_files(sources_dir: Path) -> list[dict]:
    """Get all .aaxc files with extracted ASIN and title."""
    results = []

    for aaxc_file in sources_dir.glob("*.aaxc"):
        asin, title = extract_asin_and_title(aaxc_file.name)
        if asin and title:
            results.append({
                "path": str(aaxc_file),
                "asin": asin,
                "title": title,
                "normalized_title": normalize_title(title),
            })

    print(f"📂 Found {len(results)} source files with ASINs")
    return results


def get_audiobooks_needing_asin(db_path: Path) -> list[dict]:
    """Get audiobooks without ASIN from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, file_path
        FROM audiobooks
        WHERE source = 'audible'
        AND (asin IS NULL OR asin = '')
    """)

    books = []
    for row in cursor.fetchall():
        books.append({
            "id": row["id"],
            "title": row["title"],
            "author": row["author"],
            "file_path": row["file_path"],
            "normalized_title": normalize_title(row["title"]),
        })

    conn.close()
    print(f"📖 Found {len(books)} audiobooks needing ASINs")
    return books


def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate word overlap similarity."""
    words1 = set(s1.split())
    words2 = set(s2.split())
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def match_sources_to_db(sources: list[dict], audiobooks: list[dict]) -> list[dict]:
    """Match source files to database entries by title."""
    matches = []
    unmatched_sources = []
    unmatched_books = list(audiobooks)  # Copy to track unmatched

    # Build lookup from sources by normalized title
    source_by_title = {}
    for src in sources:
        key = src["normalized_title"]
        if key not in source_by_title:
            source_by_title[key] = src

    print(f"\n🔍 Matching {len(audiobooks)} audiobooks to {len(source_by_title)} source files...\n")

    matched_ids = set()

    for book in audiobooks:
        # Try exact match first
        if book["normalized_title"] in source_by_title:
            src = source_by_title[book["normalized_title"]]
            matches.append({
                "book_id": book["id"],
                "book_title": book["title"],
                "source_title": src["title"],
                "asin": src["asin"],
                "confidence": "exact",
            })
            matched_ids.add(book["id"])
            continue

        # Try fuzzy match
        best_match = None
        best_score = 0.0

        for norm_title, src in source_by_title.items():
            score = calculate_similarity(book["normalized_title"], norm_title)
            if score > best_score and score >= 0.7:  # 70% threshold
                best_score = score
                best_match = src

        if best_match:
            matches.append({
                "book_id": book["id"],
                "book_title": book["title"],
                "source_title": best_match["title"],
                "asin": best_match["asin"],
                "confidence": f"fuzzy ({best_score:.0%})",
            })
            matched_ids.add(book["id"])
        else:
            unmatched_books.append(book)

    # Count unmatched
    unmatched_count = len(audiobooks) - len(matched_ids)

    return matches, unmatched_count


def update_database(db_path: Path, matches: list[dict], dry_run: bool = False) -> int:
    """Update database with matched ASINs."""
    if dry_run:
        print("\n🔸 DRY RUN - No changes will be made\n")

    matched = [m for m in matches if m["asin"]]

    print(f"📊 Match Results:")
    print(f"   ✅ Matched: {len(matched)}")

    if not dry_run and matched:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for match in matched:
            cursor.execute(
                "UPDATE audiobooks SET asin = ?, source_asin = ? WHERE id = ?",
                (match["asin"], match["asin"], match["book_id"])
            )

        conn.commit()
        conn.close()
        print(f"\n✅ Updated {len(matched)} audiobooks with ASINs")

    # Show sample matches
    print("\n📋 Sample Matches:")
    for m in matched[:15]:
        print(f"   [{m['confidence']}] {m['book_title'][:50]}")
        print(f"       → ASIN: {m['asin']}")

    return len(matched)


def main():
    parser = argparse.ArgumentParser(description="Populate ASINs from source filenames")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Database path")
    parser.add_argument("--sources", type=Path, default=SOURCES_DIR, help="Sources directory")
    args = parser.parse_args()

    if not args.sources.exists():
        print(f"❌ Sources directory not found: {args.sources}")
        sys.exit(1)

    # Get source files
    sources = get_source_files(args.sources)

    if not sources:
        print("❌ No source files with ASINs found")
        sys.exit(1)

    # Get audiobooks needing ASINs
    audiobooks = get_audiobooks_needing_asin(args.db)

    if not audiobooks:
        print("✅ All audiobooks already have ASINs!")
        return

    # Match
    matches, unmatched_count = match_sources_to_db(sources, audiobooks)

    print(f"\n   ❌ No match: {unmatched_count}")

    # Update
    update_database(args.db, matches, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
