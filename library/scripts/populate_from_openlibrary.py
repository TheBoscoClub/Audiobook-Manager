#!/usr/bin/env python3
"""
Enrich audiobook metadata from OpenLibrary API.

Populates genres, subjects, ISBN, and publication information for existing
audiobooks. Particularly useful for non-Audible sources that lack ASIN.

Follows existing script patterns: dry-run by default, 3-tier matching
(ISBN, exact title, fuzzy 85% threshold).

Usage:
    # Dry run - preview changes
    python3 populate_from_openlibrary.py

    # Apply changes
    python3 populate_from_openlibrary.py --execute

    # Process only books without ASIN (non-Audible)
    python3 populate_from_openlibrary.py --non-audible --execute

    # Single book by ID
    python3 populate_from_openlibrary.py --id 1234 --execute
"""

import sqlite3
import sys
import re
from pathlib import Path
from argparse import ArgumentParser
from difflib import SequenceMatcher
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

# Import OpenLibrary client
from utils.openlibrary_client import OpenLibraryClient, OpenLibraryWork

DB_PATH = DATABASE_PATH
FUZZY_THRESHOLD = 0.85


@dataclass
class EnrichmentResult:
    """Result of enriching a single audiobook."""
    audiobook_id: int
    title: str
    match_method: str  # 'isbn', 'exact_title', 'fuzzy_title', 'no_match'
    subjects_found: List[str]
    publication_year: Optional[int] = None
    isbn_found: Optional[str] = None
    work_id: Optional[str] = None
    similarity: Optional[float] = None


def normalize_title(title: str) -> str:
    """
    Normalize title for matching.

    Follows existing pattern from populate_genres.py:
    - Remove (Unabridged), [Unabridged]
    - Remove ": A Novel", ": A Memoir"
    - Remove punctuation
    - Lowercase and collapse whitespace
    """
    if not title:
        return ''
    title = re.sub(r'\s*\(Unabridged\)\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Unabridged\]\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*:\s*A Novel\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*:\s*A Memoir\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[^\w\s]', '', title)
    title = ' '.join(title.lower().split())
    return title


def similarity(a: str, b: str) -> float:
    """Calculate normalized similarity ratio."""
    return SequenceMatcher(
        None,
        normalize_title(a),
        normalize_title(b)
    ).ratio()


def populate_from_openlibrary(
    dry_run: bool = True,
    limit: Optional[int] = None,
    only_missing_genres: bool = True,
    only_non_audible: bool = False,
    audiobook_id: Optional[int] = None,
    rate_limit: float = 0.6,
    verbose: bool = False
):
    """
    Enrich audiobooks with metadata from OpenLibrary.

    Args:
        dry_run: If True, only preview changes without applying
        limit: Maximum number of audiobooks to process
        only_missing_genres: Only process books without genre data
        only_non_audible: Only process books without ASIN
        audiobook_id: Process single audiobook by ID
        rate_limit: Seconds between API requests
        verbose: Show verbose output
    """
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    # Initialize OpenLibrary client
    client = OpenLibraryClient(rate_limit_delay=rate_limit)

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Build query for candidates
    conditions = []
    params = []

    if audiobook_id:
        conditions.append("a.id = ?")
        params.append(audiobook_id)
    else:
        if only_missing_genres:
            # Books with no genre associations
            conditions.append("""
                a.id NOT IN (SELECT audiobook_id FROM audiobook_genres)
            """)
        if only_non_audible:
            conditions.append("(a.asin IS NULL OR a.asin = '')")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_clause = f"LIMIT {limit}" if limit else ""

    query = f"""
        SELECT a.id, a.title, a.author, a.asin, a.isbn, a.published_year
        FROM audiobooks a
        {where_clause}
        ORDER BY a.title
        {limit_clause}
    """

    cursor.execute(query, params)
    candidates = cursor.fetchall()

    print(f"Found {len(candidates)} audiobooks to process")

    # Track results
    results: List[EnrichmentResult] = []
    matches = []
    no_match = []
    all_subjects = set()

    for i, book in enumerate(candidates, 1):
        book_id = book['id']
        book_title = book['title']
        book_author = book['author'] or ''
        book_isbn = book['isbn']

        if verbose:
            print(f"\n[{i}/{len(candidates)}] Processing: {book_title[:50]}")

        result = None
        work = None

        # Tier 1: ISBN lookup (most reliable)
        if book_isbn:
            edition = client.lookup_isbn(book_isbn)
            if edition and edition.work_id:
                work = client.get_work(edition.work_id)
                if work and work.subjects:
                    result = EnrichmentResult(
                        audiobook_id=book_id,
                        title=book_title,
                        match_method='isbn',
                        subjects_found=work.subjects,
                        publication_year=work.first_publish_year,
                        isbn_found=book_isbn,
                        work_id=work.work_id
                    )

        # Tier 2 & 3: Title/Author search with matching
        if not result:
            search_results = client.search(title=book_title, author=book_author, limit=5)

            best_match = None
            best_ratio = 0
            best_method = 'no_match'

            for sr in search_results:
                sr_title = sr.get('title', '')
                ratio = similarity(book_title, sr_title)

                # Exact normalized match
                if normalize_title(book_title) == normalize_title(sr_title):
                    if ratio > best_ratio:
                        best_match = sr
                        best_ratio = ratio
                        best_method = 'exact_title'
                # Fuzzy match above threshold
                elif ratio >= FUZZY_THRESHOLD and ratio > best_ratio:
                    best_match = sr
                    best_ratio = ratio
                    best_method = f'fuzzy ({ratio:.0%})'

            if best_match:
                work_key = best_match.get('key', '')
                if work_key:
                    work = client.get_work(work_key)
                    if work and work.subjects:
                        # Try to get ISBN from search result
                        isbn_list = best_match.get('isbn', [])
                        found_isbn = isbn_list[0] if isbn_list else None

                        result = EnrichmentResult(
                            audiobook_id=book_id,
                            title=book_title,
                            match_method=best_method,
                            subjects_found=work.subjects,
                            publication_year=work.first_publish_year or best_match.get('first_publish_year'),
                            isbn_found=found_isbn,
                            work_id=work.work_id,
                            similarity=best_ratio if 'fuzzy' in best_method else None
                        )

        if result:
            results.append(result)
            matches.append(result)
            all_subjects.update(result.subjects_found)
        else:
            no_match.append(book_title)

    # Report results
    print()
    print("=" * 70)
    print(f"MATCHES FOUND: {len(matches)}")
    print(f"UNIQUE SUBJECTS: {len(all_subjects)}")
    print("=" * 70)

    # Show sample matches
    for m in matches[:10]:
        print(f"\n{m.title[:50]}")
        print(f"  Subjects: {', '.join(m.subjects_found[:5])}")
        print(f"  Match: {m.match_method}")
        if m.isbn_found:
            print(f"  ISBN: {m.isbn_found}")
        if m.publication_year:
            print(f"  Year: {m.publication_year}")

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

        # Get or create genres from subjects
        genre_id_map = {}

        # Get existing genres
        cursor.execute("SELECT id, name FROM genres")
        for row in cursor.fetchall():
            genre_id_map[row['name']] = row['id']

        # Insert new genres
        new_genres = all_subjects - set(genre_id_map.keys())
        for genre in sorted(new_genres):
            cursor.execute("INSERT INTO genres (name) VALUES (?)", (genre,))
            genre_id_map[genre] = cursor.lastrowid
        if new_genres:
            print(f"Inserted {len(new_genres)} new genres")

        # Update audiobooks and create genre associations
        association_count = 0
        isbn_updates = 0
        year_updates = 0

        for result in matches:
            # Update ISBN if found and not already set
            if result.isbn_found:
                cursor.execute(
                    "UPDATE audiobooks SET isbn = ? WHERE id = ? AND (isbn IS NULL OR isbn = '')",
                    (result.isbn_found, result.audiobook_id)
                )
                if cursor.rowcount > 0:
                    isbn_updates += 1

            # Update publication year if found and not already set
            if result.publication_year:
                cursor.execute(
                    "UPDATE audiobooks SET published_year = ? WHERE id = ? AND (published_year IS NULL OR published_year = 0)",
                    (result.publication_year, result.audiobook_id)
                )
                if cursor.rowcount > 0:
                    year_updates += 1

            # Create genre associations (deduplicated)
            seen_genres = set()
            for subject in result.subjects_found:
                if subject in genre_id_map and subject not in seen_genres:
                    # Check if association already exists
                    cursor.execute(
                        "SELECT 1 FROM audiobook_genres WHERE audiobook_id = ? AND genre_id = ?",
                        (result.audiobook_id, genre_id_map[subject])
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
                            (result.audiobook_id, genre_id_map[subject])
                        )
                        association_count += 1
                    seen_genres.add(subject)

        conn.commit()
        print(f"Created {association_count} audiobook-genre associations")
        print(f"Updated {isbn_updates} ISBN fields")
        print(f"Updated {year_updates} publication year fields")

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")

    conn.close()

    # Print subject summary
    if matches:
        print()
        print("=" * 70)
        print("TOP SUBJECTS FOUND:")
        print("=" * 70)

        subject_counts = {}
        for m in matches:
            for s in m.subjects_found:
                subject_counts[s] = subject_counts.get(s, 0) + 1

        for subject, count in sorted(subject_counts.items(), key=lambda x: -x[1])[:20]:
            print(f"  {count:4d}  {subject}")


def main():
    parser = ArgumentParser(
        description="Enrich audiobook metadata from OpenLibrary"
    )
    parser.add_argument('--limit', '-n', type=int, default=None,
                        help='Maximum audiobooks to process')
    parser.add_argument('--only-missing', action='store_true', default=True,
                        help='Only process books without genre data (default)')
    parser.add_argument('--all', action='store_true',
                        help='Process all audiobooks (refresh existing data)')
    parser.add_argument('--non-audible', action='store_true',
                        help='Only process books without ASIN')
    parser.add_argument('--id', type=int, default=None,
                        help='Process single audiobook by ID')
    parser.add_argument('--rate-limit', type=float, default=0.6,
                        help='Seconds between API requests (default: 0.6)')
    parser.add_argument('--execute', action='store_true',
                        help='Actually apply changes (default is dry run)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show verbose output')

    args = parser.parse_args()

    populate_from_openlibrary(
        dry_run=not args.execute,
        limit=args.limit,
        only_missing_genres=not args.all,
        only_non_audible=args.non_audible,
        audiobook_id=args.id,
        rate_limit=args.rate_limit,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
