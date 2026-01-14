#!/usr/bin/env python3
"""
Populate ASINs in the audiobook database by matching against Audible library.

This script:
1. Fetches your full Audible library
2. Matches local audiobooks by title (fuzzy matching)
3. Updates the ASIN field in the database

Usage:
    python populate_asins.py --dry-run    # Preview matches without updating
    python populate_asins.py              # Update database with ASINs
"""

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

try:
    import audible
except ImportError:
    print("ERROR: 'audible' library not installed")
    sys.exit(1)

from credential_manager import retrieve_credential

# Add library directory to path for common import
sys.path.insert(0, str(Path(__file__).parent.parent / "library"))
from common import normalize_title
from config import AUDIOBOOKS_DATABASE

# Configuration - use bosco's audible config even when running as root
REAL_USER_HOME = Path(os.environ.get("SUDO_USER_HOME", os.environ.get("HOME", "/home/bosco")))
if os.environ.get("SUDO_USER"):
    REAL_USER_HOME = Path(f"/home/{os.environ['SUDO_USER']}")
AUDIBLE_CONFIG_DIR = REAL_USER_HOME / ".audible"
AUTH_FILE = AUDIBLE_CONFIG_DIR / "audible.json"
CREDENTIAL_FILE_PATH = AUDIBLE_CONFIG_DIR / "position_sync_credentials.enc"
DB_PATH = AUDIOBOOKS_DATABASE
COUNTRY_CODE = "us"


def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate simple word overlap similarity."""
    words1 = set(normalize_title(s1).split())
    words2 = set(normalize_title(s2).split())
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


async def fetch_audible_library(client) -> list[dict]:
    """Fetch complete Audible library (audiobooks only, not periodicals).

    Filters by content_type='Product' to exclude podcasts, newspapers,
    shows, and other periodical content that belongs in Reading Room.
    """
    print("📚 Fetching Audible library (audiobooks only)...")

    all_items = []
    page = 1  # Audible API is 1-indexed
    page_size = 50
    skipped_periodicals = 0

    while True:
        response = await client.get(
            "1.0/library",
            params={
                "num_results": page_size,
                "page": page,
                "response_groups": "product_desc,product_attrs",
            }
        )

        items = response.get("items", [])
        if not items:
            break

        # Filter to only include actual audiobooks (content_type='Product')
        # Exclude periodicals: Podcast, Newspaper / Magazine, Show, Radio/TV Program
        for item in items:
            content_type = item.get("content_type", "Product")
            if content_type == "Product":
                # Store content_type for reference
                item["_content_type"] = content_type
                all_items.append(item)
            else:
                skipped_periodicals += 1

        print(f"   Fetched {len(all_items)} audiobooks (skipped {skipped_periodicals} periodicals)...")

        if len(items) < page_size:
            break
        page += 1

    print(f"✅ Fetched {len(all_items)} audiobooks from Audible library")
    if skipped_periodicals:
        print(f"   ℹ️  Skipped {skipped_periodicals} periodicals (belong in Reading Room)")
    return all_items


def get_local_audiobooks(db_path: Path) -> list[dict]:
    """Get all local audiobooks without ASINs."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author
        FROM audiobooks
        WHERE (asin IS NULL OR asin = '')
        AND source = 'audible'
    """)

    books = [dict(row) for row in cursor.fetchall()]
    conn.close()

    print(f"📖 Found {len(books)} local audiobooks without ASINs")
    return books


def match_books(local_books: list[dict], audible_library: list[dict]) -> list[dict]:
    """Match local books to Audible library by title."""
    matches = []

    # Build lookup from Audible library
    audible_by_title = {}
    for item in audible_library:
        asin = item.get("asin")
        title = item.get("title", "")
        if asin and title:
            normalized = normalize_title(title)
            authors = item.get("authors") or []
            audible_by_title[normalized] = {
                "asin": asin,
                "title": title,
                "author": authors[0].get("name", "") if authors else "",
            }

    print(f"\n🔍 Matching {len(local_books)} local books against {len(audible_by_title)} Audible titles...\n")

    for local in local_books:
        local_title = local["title"]
        local_normalized = normalize_title(local_title)

        # Try exact match first
        if local_normalized in audible_by_title:
            match = audible_by_title[local_normalized]
            matches.append({
                "local_id": local["id"],
                "local_title": local_title,
                "audible_title": match["title"],
                "asin": match["asin"],
                "confidence": "exact",
            })
            continue

        # Try fuzzy match
        best_match = None
        best_score = 0.0

        for norm_title, audible_item in audible_by_title.items():
            score = calculate_similarity(local_normalized, norm_title)
            if score > best_score and score >= 0.7:  # 70% threshold
                best_score = score
                best_match = audible_item

        if best_match:
            matches.append({
                "local_id": local["id"],
                "local_title": local_title,
                "audible_title": best_match["title"],
                "asin": best_match["asin"],
                "confidence": f"fuzzy ({best_score:.0%})",
            })
        else:
            matches.append({
                "local_id": local["id"],
                "local_title": local_title,
                "audible_title": None,
                "asin": None,
                "confidence": "no match",
            })

    return matches


def update_database(db_path: Path, matches: list[dict], dry_run: bool = False) -> int:
    """Update database with matched ASINs."""
    if dry_run:
        print("\n🔸 DRY RUN - No changes will be made\n")

    updates = [m for m in matches if m["asin"]]
    no_match = [m for m in matches if not m["asin"]]

    print(f"📊 Match Results:")
    print(f"   ✅ Matched: {len(updates)}")
    print(f"   ❌ No match: {len(no_match)}")

    if not dry_run and updates:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for match in updates:
            # Set both ASIN and content_type='Product' to ensure proper classification
            # This marks the item as a true audiobook, not a periodical
            cursor.execute(
                "UPDATE audiobooks SET asin = ?, content_type = 'Product' WHERE id = ?",
                (match["asin"], match["local_id"])
            )

        conn.commit()
        conn.close()
        print(f"\n✅ Updated {len(updates)} audiobooks with ASINs (marked as content_type='Product')")

    # Show sample matches
    print("\n📋 Sample Matches:")
    for m in updates[:10]:
        print(f"   [{m['confidence']}] {m['local_title'][:50]}")
        print(f"       → {m['asin']}: {m['audible_title'][:50]}")

    if no_match:
        print("\n📋 Sample Unmatched:")
        for m in no_match[:5]:
            print(f"   ❌ {m['local_title'][:60]}")

    return len(updates)


async def main():
    parser = argparse.ArgumentParser(description="Populate ASINs from Audible library")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    # Get credential from the correct user's credential file
    password = retrieve_credential(credential_file=CREDENTIAL_FILE_PATH)
    if not password:
        print(f"ERROR: No stored credential found at {CREDENTIAL_FILE_PATH}")
        print("Run 'python rnd/position_sync_test.py list' first to set up credentials.")
        sys.exit(1)

    print(f"🔓 Using credential from {CREDENTIAL_FILE_PATH}")

    # Authenticate
    auth = audible.Authenticator.from_file(AUTH_FILE, password=password)

    async with audible.AsyncClient(auth=auth, country_code=COUNTRY_CODE) as client:
        # Fetch Audible library
        audible_library = await fetch_audible_library(client)

        # Get local books
        local_books = get_local_audiobooks(args.db)

        if not local_books:
            print("✅ All local audiobooks already have ASINs!")
            return

        # Match
        matches = match_books(local_books, audible_library)

        # Update
        update_database(args.db, matches, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
