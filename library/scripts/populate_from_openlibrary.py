#!/usr/bin/env python3
"""
EXPERIMENTAL - ROUGHED IN, NOT FULLY TESTED
============================================
This script is part of the multi-source audiobook support feature which has been
moved to "Phase Maybe" in the roadmap. The code exists and may work, but it is
not actively supported or prioritized.

The core purpose of Audiobook-Manager is managing Audible audiobooks. Multi-source
support (Google Play, Librivox, etc.) was roughed in but is not the project's focus.

If you want to use or finish this feature, you're welcome to - PRs accepted.
See: https://github.com/TheBoscoClub/Audiobook-Manager/discussions/2
============================================

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
from argparse import ArgumentParser
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import normalize_title

# Import OpenLibrary client
from utils.openlibrary_client import OpenLibraryClient

from config import DATABASE_PATH

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


def similarity(a: str, b: str) -> float:
    """Calculate normalized similarity ratio."""
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _build_candidate_query(
    audiobook_id: Optional[int],
    only_missing_genres: bool,
    only_non_audible: bool,
    limit: Optional[int],
) -> tuple[str, list]:
    """Build SQL query for candidate books."""
    conditions = []
    params = []

    if audiobook_id:
        conditions.append("a.id = ?")
        params.append(audiobook_id)
    else:
        if only_missing_genres:
            conditions.append("a.id NOT IN (SELECT audiobook_id FROM audiobook_genres)")
        if only_non_audible:
            conditions.append("(a.asin IS NULL OR a.asin = '')")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_clause = f"LIMIT {limit}" if limit else ""

    query = (
        "SELECT a.id, a.title, a.author, a.asin, a.isbn, a.published_year"  # nosec B608
        " FROM audiobooks a"
        f" {where_clause}"
        " ORDER BY a.title"
        f" {limit_clause}"
    )
    return query, params


def _try_isbn_lookup(
    client: OpenLibraryClient, book_id: int, book_title: str, book_isbn: str
) -> Optional[EnrichmentResult]:
    """Tier 1: ISBN lookup (most reliable)."""
    edition = client.lookup_isbn(book_isbn)
    if not edition or not edition.work_id:
        return None
    work = client.get_work(edition.work_id)
    if not work or not work.subjects:
        return None
    return EnrichmentResult(
        audiobook_id=book_id,
        title=book_title,
        match_method="isbn",
        subjects_found=work.subjects,
        publication_year=work.first_publish_year,
        isbn_found=book_isbn,
        work_id=work.work_id,
    )


def _find_best_title_match(
    search_results: list, book_title: str
) -> tuple[Optional[dict], float, str]:
    """Find best title match from search results.

    Returns (best_match, best_ratio, best_method).
    """
    best_match = None
    best_ratio = 0
    best_method = "no_match"

    for sr in search_results:
        sr_title = sr.get("title", "")
        ratio = similarity(book_title, sr_title)

        if normalize_title(book_title) == normalize_title(sr_title):
            if ratio > best_ratio:
                best_match = sr
                best_ratio = int(ratio * 100)
                best_method = "exact_title"
        elif ratio >= FUZZY_THRESHOLD and ratio > best_ratio / 100:
            best_match = sr
            best_ratio = int(ratio * 100)
            best_method = f"fuzzy ({ratio:.0%})"

    return best_match, best_ratio, best_method


def _try_title_search(
    client: OpenLibraryClient, book_id: int, book_title: str, book_author: str
) -> Optional[EnrichmentResult]:
    """Tier 2 & 3: Title/Author search with matching."""
    search_results = client.search(title=book_title, author=book_author, limit=5)
    best_match, best_ratio, best_method = _find_best_title_match(search_results, book_title)

    if not best_match:
        return None

    work_key = best_match.get("key", "")
    if not work_key:
        return None

    work = client.get_work(work_key)
    if not work or not work.subjects:
        return None

    isbn_list = best_match.get("isbn", [])
    found_isbn = isbn_list[0] if isbn_list else None

    return EnrichmentResult(
        audiobook_id=book_id,
        title=book_title,
        match_method=best_method,
        subjects_found=work.subjects,
        publication_year=(work.first_publish_year or best_match.get("first_publish_year")),
        isbn_found=found_isbn,
        work_id=work.work_id,
        similarity=best_ratio if "fuzzy" in best_method else None,
    )


def _match_book(
    client: OpenLibraryClient, book: dict, verbose: bool, idx: int, total: int
) -> Optional[EnrichmentResult]:
    """Attempt to match a single book via ISBN or title search."""
    book_id = book["id"]
    book_title = book["title"]
    book_author = book["author"] or ""
    book_isbn = book["isbn"]

    if verbose:
        print(f"\n[{idx}/{total}] Processing: {book_title[:50]}")

    # Tier 1: ISBN lookup
    if book_isbn:
        result = _try_isbn_lookup(client, book_id, book_title, book_isbn)
        if result:
            return result

    # Tier 2 & 3: Title/Author search
    return _try_title_search(client, book_id, book_title, book_author)


def _print_match_report(
    matches: list[EnrichmentResult], no_match: list[str], all_subjects: set
) -> None:
    """Print matching results report."""
    print()
    print("=" * 70)
    print(f"MATCHES FOUND: {len(matches)}")
    print(f"UNIQUE SUBJECTS: {len(all_subjects)}")
    print("=" * 70)

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


def _apply_genre_updates(cursor, matches: list[EnrichmentResult], all_subjects: set) -> None:
    """Apply genre and metadata updates to the database."""
    genre_id_map = {}

    # Get existing genres
    cursor.execute("SELECT id, name FROM genres")
    for row in cursor.fetchall():
        genre_id_map[row["name"]] = row["id"]

    # Insert new genres
    new_genres = all_subjects - set(genre_id_map.keys())
    for genre in sorted(new_genres):
        cursor.execute("INSERT INTO genres (name) VALUES (?)", (genre,))
        genre_id_map[genre] = cursor.lastrowid
    if new_genres:
        print(f"Inserted {len(new_genres)} new genres")

    association_count = 0
    isbn_updates = 0
    year_updates = 0

    for result in matches:
        isbn_updates += _update_isbn_if_missing(cursor, result)
        year_updates += _update_year_if_missing(cursor, result)
        association_count += _create_genre_associations(cursor, result, genre_id_map)

    print(f"Created {association_count} audiobook-genre associations")
    print(f"Updated {isbn_updates} ISBN fields")
    print(f"Updated {year_updates} publication year fields")


def _update_isbn_if_missing(cursor, result: EnrichmentResult) -> int:
    """Update ISBN if found and not already set. Returns 1 if updated."""
    if not result.isbn_found:
        return 0
    cursor.execute(
        "UPDATE audiobooks SET isbn = ? WHERE id = ? AND (isbn IS NULL OR isbn = '')",
        (result.isbn_found, result.audiobook_id),
    )
    return cursor.rowcount


def _update_year_if_missing(cursor, result: EnrichmentResult) -> int:
    """Update publication year if found and not already set. Returns 1 if updated."""
    if not result.publication_year:
        return 0
    cursor.execute(
        "UPDATE audiobooks SET published_year = ? WHERE id = ?"
        " AND (published_year IS NULL OR published_year = 0)",
        (result.publication_year, result.audiobook_id),
    )
    return cursor.rowcount


def _create_genre_associations(cursor, result: EnrichmentResult, genre_id_map: dict) -> int:
    """Create genre associations for a book. Returns count of new associations."""
    count = 0
    seen_genres = set()
    for subject in result.subjects_found:
        if subject not in genre_id_map or subject in seen_genres:
            continue
        cursor.execute(
            "SELECT 1 FROM audiobook_genres WHERE audiobook_id = ? AND genre_id = ?",
            (result.audiobook_id, genre_id_map[subject]),
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
                (result.audiobook_id, genre_id_map[subject]),
            )
            count += 1
        seen_genres.add(subject)
    return count


def _print_subject_summary(matches: list[EnrichmentResult]) -> None:
    """Print top subjects found across all matches."""
    print()
    print("=" * 70)
    print("TOP SUBJECTS FOUND:")
    print("=" * 70)

    subject_counts: dict[str, int] = {}
    for m in matches:
        for s in m.subjects_found:
            subject_counts[s] = subject_counts.get(s, 0) + 1

    for subject, count in sorted(subject_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {count:4d}  {subject}")


def populate_from_openlibrary(
    dry_run: bool = True,
    limit: Optional[int] = None,
    only_missing_genres: bool = True,
    only_non_audible: bool = False,
    audiobook_id: Optional[int] = None,
    rate_limit: float = 0.6,
    verbose: bool = False,
):
    """Enrich audiobooks with metadata from OpenLibrary."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    client = OpenLibraryClient(rate_limit_delay=rate_limit)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query, params = _build_candidate_query(
        audiobook_id, only_missing_genres, only_non_audible, limit
    )
    cursor.execute(query, params)
    candidates = cursor.fetchall()
    print(f"Found {len(candidates)} audiobooks to process")

    matches: List[EnrichmentResult] = []
    no_match: list[str] = []
    all_subjects: set[str] = set()

    for i, book in enumerate(candidates, 1):
        result = _match_book(client, dict(book), verbose, i, len(candidates))
        if result:
            matches.append(result)
            all_subjects.update(result.subjects_found)
        else:
            no_match.append(book["title"])

    _print_match_report(matches, no_match, all_subjects)

    if not dry_run and matches:
        print()
        print("=" * 70)
        print("APPLYING UPDATES...")
        print("=" * 70)
        _apply_genre_updates(cursor, matches, all_subjects)
        conn.commit()

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")

    conn.close()

    if matches:
        _print_subject_summary(matches)


def main():
    parser = ArgumentParser(description="Enrich audiobook metadata from OpenLibrary")
    parser.add_argument(
        "--limit", "-n", type=int, default=None, help="Maximum audiobooks to process"
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        default=True,
        help="Only process books without genre data (default)",
    )
    parser.add_argument(
        "--all", action="store_true", help="Process all audiobooks (refresh existing data)"
    )
    parser.add_argument(
        "--non-audible", action="store_true", help="Only process books without ASIN"
    )
    parser.add_argument("--id", type=int, default=None, help="Process single audiobook by ID")
    parser.add_argument(
        "--rate-limit", type=float, default=0.6, help="Seconds between API requests (default: 0.6)"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually apply changes (default is dry run)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show verbose output")

    args = parser.parse_args()

    populate_from_openlibrary(
        dry_run=not args.execute,
        limit=args.limit,
        only_missing_genres=not args.all,
        only_non_audible=args.non_audible,
        audiobook_id=args.id,
        rate_limit=args.rate_limit,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
