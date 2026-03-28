#!/usr/bin/env python3
"""
Populate name-split columns and rebuild author/narrator junction tables.

After a database reimport, the name-split columns (author_last_name, etc.)
are NULL and the junction tables (book_authors, book_narrators) are orphaned
because audiobook IDs change. This script rebuilds them from the flat
author/narrator fields using the name_parser module.

Usage:
    python3 populate_names.py              # dry run
    python3 populate_names.py --execute    # apply changes
"""

import sqlite3
import sys
from argparse import ArgumentParser
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
from config import DATABASE_PATH

from name_parser import (
    clean_name,
    generate_sort_name,
    is_brand_name,
    is_junk_name,
    normalize_for_dedup,
    parse_names,
)


def populate_name_columns(conn: sqlite3.Connection) -> int:
    """Populate author/narrator first/last name columns from flat fields.

    Returns count of updated rows.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id, author, narrator FROM audiobooks")
    rows = cursor.fetchall()

    updated = 0
    for row in rows:
        book_id, author_raw, narrator_raw = row[0], row[1], row[2]

        # Extract primary author name (first in list)
        author_first = None
        author_last = None
        if author_raw and author_raw.strip():
            names = parse_names(author_raw)
            if names:
                primary = names[0]
                sort_name = generate_sort_name(primary)
                if sort_name and ", " in sort_name:
                    parts = sort_name.split(", ", 1)
                    author_last = parts[0]
                    author_first = parts[1]
                elif sort_name:
                    author_last = sort_name

        # Extract primary narrator name (first in list)
        narrator_first = None
        narrator_last = None
        if narrator_raw and narrator_raw.strip():
            names = parse_names(narrator_raw)
            if names:
                primary = names[0]
                sort_name = generate_sort_name(primary)
                if sort_name and ", " in sort_name:
                    parts = sort_name.split(", ", 1)
                    narrator_last = parts[0]
                    narrator_first = parts[1]
                elif sort_name:
                    narrator_last = sort_name

        cursor.execute(
            "UPDATE audiobooks SET author_last_name=?, author_first_name=?, "
            "narrator_last_name=?, narrator_first_name=? WHERE id=?",
            (author_last, author_first, narrator_last, narrator_first, book_id),
        )
        updated += 1

    return updated


def rebuild_junction_tables(conn: sqlite3.Connection) -> tuple[int, int]:
    """Rebuild authors/narrators tables and book_authors/book_narrators junctions.

    Returns (author_count, narrator_count).
    """
    cursor = conn.cursor()

    # Clear existing junction data
    cursor.execute("DELETE FROM book_authors")
    cursor.execute("DELETE FROM book_narrators")
    cursor.execute("DELETE FROM authors")
    cursor.execute("DELETE FROM narrators")

    # Fetch all audiobooks
    cursor.execute("SELECT id, author, narrator FROM audiobooks")
    rows = cursor.fetchall()

    # Build author and narrator maps
    authors_map: dict[str, int] = {}  # dedup_key -> author_id
    narrators_map: dict[str, int] = {}  # dedup_key -> narrator_id

    for row in rows:
        book_id, author_raw, narrator_raw = row[0], row[1], row[2]

        # Process authors
        if author_raw and author_raw.strip():
            names = parse_names(author_raw)
            for position, name in enumerate(names):
                if is_junk_name(name) or is_brand_name(name):
                    continue
                cleaned = clean_name(name)
                if not cleaned:
                    continue

                dedup_key = normalize_for_dedup(cleaned)
                if dedup_key not in authors_map:
                    sort_name = generate_sort_name(cleaned)
                    if not sort_name:
                        sort_name = cleaned
                    cursor.execute(
                        "INSERT INTO authors (name, sort_name) VALUES (?, ?)",
                        (cleaned, sort_name),
                    )
                    authors_map[dedup_key] = cursor.lastrowid

                author_id = authors_map[dedup_key]
                try:
                    cursor.execute(
                        "INSERT INTO book_authors (book_id, author_id, position) "
                        "VALUES (?, ?, ?)",
                        (book_id, author_id, position),
                    )
                except sqlite3.IntegrityError:
                    pass  # Duplicate — same author linked twice to same book

        # Process narrators
        if narrator_raw and narrator_raw.strip():
            names = parse_names(narrator_raw)
            for position, name in enumerate(names):
                if is_junk_name(name) or is_brand_name(name):
                    continue
                cleaned = clean_name(name)
                if not cleaned:
                    continue

                dedup_key = normalize_for_dedup(cleaned)
                if dedup_key not in narrators_map:
                    sort_name = generate_sort_name(cleaned)
                    if not sort_name:
                        sort_name = cleaned
                    cursor.execute(
                        "INSERT INTO narrators (name, sort_name) VALUES (?, ?)",
                        (cleaned, sort_name),
                    )
                    narrators_map[dedup_key] = cursor.lastrowid

                narrator_id = narrators_map[dedup_key]
                try:
                    cursor.execute(
                        "INSERT INTO book_narrators (book_id, narrator_id, position) "
                        "VALUES (?, ?, ?)",
                        (book_id, narrator_id, position),
                    )
                except sqlite3.IntegrityError:
                    pass

    return len(authors_map), len(narrators_map)


def main():
    parser = ArgumentParser(description="Populate name columns and rebuild junction tables")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes (default is dry run)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to database (default: config DATABASE_PATH)",
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DATABASE_PATH
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    if args.execute:
        conn = sqlite3.connect(db_path)
    else:
        # Open read-only for dry run (avoids permission issues on production)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # Check current state
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM audiobooks WHERE author_last_name IS NOT NULL"
    )
    has_last = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM book_authors")
    ba_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM book_narrators")
    bn_count = cursor.fetchone()[0]

    print(f"Database: {db_path}")
    print(f"Total audiobooks: {total}")
    print(f"With author_last_name populated: {has_last}")
    print(f"book_authors rows: {ba_count}")
    print(f"book_narrators rows: {bn_count}")
    print()

    if not args.execute:
        # Dry run — show what would happen
        print("=== DRY RUN ===\n")

        # Sample name parsing
        cursor.execute(
            "SELECT id, author, narrator FROM audiobooks LIMIT 10"
        )
        for row in cursor.fetchall():
            author_raw = row["author"] or ""
            narrator_raw = row["narrator"] or ""
            a_names = parse_names(author_raw) if author_raw else []
            n_names = parse_names(narrator_raw) if narrator_raw else []
            a_sort = generate_sort_name(a_names[0]) if a_names else ""
            n_sort = generate_sort_name(n_names[0]) if n_names else ""
            print(f"  Author: {author_raw[:40]:40s} -> {a_sort}")
            print(f"  Narrator: {narrator_raw[:40]:40s} -> {n_sort}")
            print()

        print("Run with --execute to apply changes.")
        conn.close()
        return

    # Execute
    print("Populating name-split columns...")
    updated = populate_name_columns(conn)
    print(f"  Updated {updated} audiobooks")

    print("\nRebuilding junction tables...")
    author_count, narrator_count = rebuild_junction_tables(conn)
    print(f"  Created {author_count} unique authors")
    print(f"  Created {narrator_count} unique narrators")

    # Verify
    cursor.execute("SELECT COUNT(*) FROM book_authors")
    ba_new = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM book_narrators")
    bn_new = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM audiobooks WHERE author_last_name IS NOT NULL"
    )
    has_last_new = cursor.fetchone()[0]

    print(f"\n  book_authors rows: {ba_count} -> {ba_new}")
    print(f"  book_narrators rows: {bn_count} -> {bn_new}")
    print(f"  With author_last_name: {has_last} -> {has_last_new}")

    # Check orphans
    cursor.execute(
        "SELECT COUNT(*) FROM audiobooks WHERE id NOT IN "
        "(SELECT book_id FROM book_authors)"
    )
    orphan_authors = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM audiobooks WHERE id NOT IN "
        "(SELECT book_id FROM book_narrators)"
    )
    orphan_narrators = cursor.fetchone()[0]
    print(f"\n  Orphan books (no author junction): {orphan_authors}")
    print(f"  Orphan books (no narrator junction): {orphan_narrators}")

    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
