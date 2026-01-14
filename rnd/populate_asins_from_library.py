#!/usr/bin/env python3
"""
Populate ASINs from Audible library export.

Uses the audible-cli library export (JSON) which contains the definitive
ASIN-to-title mapping directly from Amazon's database. This is more reliable
than filename extraction for audiobooks whose source files were deleted.

Usage:
    audible library export --format json --output /tmp/audible-library.json --resolve-podcasts
    python3 populate_asins_from_library.py --library /tmp/audible-library.json [--dry-run]
"""

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "library"))
from common import normalize_title
from config import AUDIOBOOKS_DATABASE

DB_PATH = AUDIOBOOKS_DATABASE


def load_audible_library(library_path: Path) -> list[dict]:
    """Load Audible library export from JSON file."""
    with open(library_path) as f:
        data = json.load(f)
    print(f"📚 Loaded {len(data)} items from Audible library")
    return data


def build_library_index(library: list[dict]) -> dict:
    """Build lookup indices for fast matching.

    Returns dict with:
        - by_normalized_title: {normalized_title: [items...]}
        - by_asin: {asin: item}
    """
    by_title = defaultdict(list)
    by_asin = {}

    for item in library:
        asin = item.get("asin")
        title = item.get("title", "")

        if asin:
            by_asin[asin] = item
            norm_title = normalize_title(title)
            if norm_title:
                by_title[norm_title].append(item)

    print(f"   📖 {len(by_asin)} unique ASINs")
    print(f"   📖 {len(by_title)} unique normalized titles")
    return {"by_title": by_title, "by_asin": by_asin}


def get_audiobooks_needing_asin(db_path: Path) -> list[dict]:
    """Get audiobooks without ASIN from database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, file_path, content_type
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
            "content_type": row["content_type"],
            "normalized_title": normalize_title(row["title"]),
        })

    conn.close()
    print(f"📖 Found {len(books)} audiobooks needing ASINs")
    return books


def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate Jaccard similarity (word overlap)."""
    words1 = set(s1.split())
    words2 = set(s2.split())
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def match_books_to_library(
    audiobooks: list[dict],
    library_index: dict,
    threshold: float = 0.6
) -> tuple[list[dict], list[dict]]:
    """Match audiobooks to library items.

    Returns (matches, unmatched).
    """
    matches = []
    unmatched = []
    by_title = library_index["by_title"]

    for book in audiobooks:
        norm_title = book["normalized_title"]

        # Try exact normalized title match
        if norm_title in by_title:
            lib_items = by_title[norm_title]
            # Use first match (they should all have same ASIN for same title)
            item = lib_items[0]
            matches.append({
                "book_id": book["id"],
                "book_title": book["title"],
                "library_title": item.get("title"),
                "asin": item.get("asin"),
                "confidence": "exact",
                "score": 1.0,
            })
            continue

        # Try fuzzy match
        best_match = None
        best_score = 0.0
        best_lib_title = None
        best_confidence = "fuzzy"

        for lib_norm_title, lib_items in by_title.items():
            score = calculate_similarity(norm_title, lib_norm_title)
            if score > best_score:
                best_score = score
                best_match = lib_items[0]
                best_lib_title = lib_norm_title
                best_confidence = "fuzzy"

            # Also check containment: if library title is contained in db title
            # E.g., "the crossing" in "the crossing harry bosch book 18"
            # Or "playback" in "playback a philip marlowe novel"
            # BUT NOT: "series name" in "ep 1 series name" (episode vs parent)
            lib_words = lib_norm_title.split()
            if lib_norm_title in norm_title and len(lib_words) >= 1:
                # Skip containment match if DB title looks like an episode
                # but library title doesn't (prevents parent→episode mismatch)
                db_has_episode = any(w in norm_title for w in ['ep ', 'episode '])
                lib_has_episode = any(w in lib_norm_title for w in ['ep ', 'episode '])
                if db_has_episode and not lib_has_episode:
                    continue  # Skip this containment match

                # Score based on word coverage
                containment_score = len(lib_words) / len(norm_title.split())
                # Boost if library title starts at the beginning of db title
                if norm_title.startswith(lib_norm_title):
                    containment_score = max(containment_score, 0.5)
                if containment_score > best_score and containment_score >= 0.2:
                    best_score = containment_score
                    best_match = lib_items[0]
                    best_lib_title = lib_norm_title
                    best_confidence = "containment"

        if best_match and best_score >= threshold:
            matches.append({
                "book_id": book["id"],
                "book_title": book["title"],
                "library_title": best_match.get("title"),
                "asin": best_match.get("asin"),
                "confidence": f"{best_confidence} ({best_score:.0%})",
                "score": best_score,
            })
        else:
            unmatched.append({
                **book,
                "best_score": best_score,
                "best_match": best_match.get("title") if best_match else None,
            })

    return matches, unmatched


def update_database(db_path: Path, matches: list[dict], dry_run: bool = False) -> int:
    """Update database with matched ASINs."""
    if dry_run:
        print("\n🔸 DRY RUN - No changes will be made\n")

    print(f"\n📊 Match Results:")
    print(f"   ✅ Matched: {len(matches)}")

    if not dry_run and matches:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for match in matches:
            cursor.execute(
                "UPDATE audiobooks SET asin = ?, source_asin = ? WHERE id = ?",
                (match["asin"], match["asin"], match["book_id"])
            )

        conn.commit()
        conn.close()
        print(f"\n✅ Updated {len(matches)} audiobooks with ASINs")

    # Show sample matches grouped by confidence
    exact = [m for m in matches if m["confidence"] == "exact"]
    fuzzy = [m for m in matches if m["confidence"] != "exact"]

    print(f"\n📋 Exact Matches ({len(exact)}):")
    for m in exact[:10]:
        print(f"   {m['book_title'][:60]}")
        print(f"       → ASIN: {m['asin']}")

    if fuzzy:
        print(f"\n📋 Fuzzy Matches ({len(fuzzy)}):")
        for m in sorted(fuzzy, key=lambda x: x["score"], reverse=True)[:10]:
            print(f"   [{m['confidence']}] {m['book_title'][:50]}")
            print(f"       → {m['library_title'][:50]}")
            print(f"       → ASIN: {m['asin']}")

    return len(matches)


def analyze_unmatched(unmatched: list[dict]):
    """Analyze and report on unmatched audiobooks."""
    print(f"\n❌ Unmatched: {len(unmatched)}")

    # Group by content type
    by_type = defaultdict(list)
    for book in unmatched:
        by_type[book.get("content_type", "Unknown")].append(book)

    print("\n📊 Unmatched by content type:")
    for ctype, books in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"   {ctype}: {len(books)}")

    # Show samples with best scores
    print("\n📋 Sample unmatched (with best potential matches):")
    for book in sorted(unmatched, key=lambda x: x.get("best_score", 0), reverse=True)[:15]:
        print(f"   {book['title'][:60]}")
        if book.get("best_match"):
            print(f"       Best match ({book['best_score']:.0%}): {book['best_match'][:50]}")
        else:
            print(f"       No potential matches found")


def main():
    parser = argparse.ArgumentParser(description="Populate ASINs from Audible library export")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Database path")
    parser.add_argument("--library", type=Path, required=True, help="Audible library JSON export")
    parser.add_argument("--threshold", type=float, default=0.6, help="Fuzzy match threshold (default 0.6)")
    args = parser.parse_args()

    if not args.library.exists():
        print(f"❌ Library file not found: {args.library}")
        print("   Generate with: audible library export --format json --output library.json --resolve-podcasts")
        sys.exit(1)

    # Load library
    library = load_audible_library(args.library)
    library_index = build_library_index(library)

    # Get audiobooks needing ASINs
    audiobooks = get_audiobooks_needing_asin(args.db)

    if not audiobooks:
        print("✅ All audiobooks already have ASINs!")
        return

    # Match
    matches, unmatched = match_books_to_library(audiobooks, library_index, args.threshold)

    # Update
    update_database(args.db, matches, dry_run=args.dry_run)

    # Analyze unmatched
    if unmatched:
        analyze_unmatched(unmatched)


if __name__ == "__main__":
    main()
