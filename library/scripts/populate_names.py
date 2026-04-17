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


def _extract_primary_name_parts(raw_name):
    """Parse a raw name string and return (first, last) for the primary name.

    Returns (None, None) if the name is empty or unparsable.
    """
    if not raw_name or not raw_name.strip():
        return None, None

    names = parse_names(raw_name)
    if not names:
        return None, None

    sort_name = generate_sort_name(names[0])
    if sort_name and ", " in sort_name:
        parts = sort_name.split(", ", 1)
        return parts[1], parts[0]  # first, last
    if sort_name:
        return None, sort_name
    return None, None


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

        author_first, author_last = _extract_primary_name_parts(author_raw)
        narrator_first, narrator_last = _extract_primary_name_parts(narrator_raw)

        cursor.execute(
            "UPDATE audiobooks SET author_last_name=?, author_first_name=?, "
            "narrator_last_name=?, narrator_first_name=? WHERE id=?",
            (author_last, author_first, narrator_last, narrator_first, book_id),
        )
        updated += 1

    return updated


def _process_person_names(raw_name, person_map, cursor, table_name):
    """Parse, deduplicate, and insert person names into the given table.

    Args:
        raw_name: Raw comma-separated name string
        person_map: Dict mapping dedup_key -> person_id (mutated in place)
        cursor: DB cursor
        table_name: 'authors' or 'narrators'

    Returns list of (person_id, position) tuples.
    """
    if not raw_name or not raw_name.strip():
        return []

    results = []
    names = parse_names(raw_name)

    for position, name in enumerate(names):
        if is_junk_name(name) or is_brand_name(name):
            continue
        cleaned = clean_name(name)
        if not cleaned:
            continue

        dedup_key = normalize_for_dedup(cleaned)
        if dedup_key not in person_map:
            sort_name = generate_sort_name(cleaned) or cleaned
            cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                f"INSERT INTO {table_name} (name, sort_name) VALUES (?, ?)",  # nosec B608
                (cleaned, sort_name),
            )
            person_map[dedup_key] = cursor.lastrowid

        results.append((person_map[dedup_key], position))

    return results


def _link_book_persons(cursor, book_id, person_entries, junction_table):
    """Insert book-person junction rows, ignoring duplicates.

    Args:
        cursor: DB cursor
        book_id: The audiobook ID
        person_entries: List of (person_id, position) from _process_person_names
        junction_table: 'book_authors' or 'book_narrators'
    """
    id_col = "author_id" if "author" in junction_table else "narrator_id"
    for person_id, position in person_entries:
        try:
            cursor.execute(
                f"INSERT INTO {junction_table} (book_id, {id_col}, position) "  # nosec B608
                "VALUES (?, ?, ?)",
                (book_id, person_id, position),
            )
        except sqlite3.IntegrityError:
            pass  # Duplicate -- same person linked twice to same book


def rebuild_junction_tables(conn: sqlite3.Connection) -> tuple[int, int]:
    """Rebuild authors/narrators tables and book_authors/book_narrators junctions.

    Returns (author_count, narrator_count).
    """
    cursor = conn.cursor()

    # Clear existing junction data
    # `table` comes from a hardcoded literal tuple of 4 schema-owned identifier
    # names; no user input reaches the format string. SQLite does not permit
    # identifiers as bound parameters, so this f-string is the only correct form.
    for table in ("book_authors", "book_narrators", "authors", "narrators"):
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        cursor.execute(f"DELETE FROM {table}")  # nosec B608

    cursor.execute("SELECT id, author, narrator FROM audiobooks")
    rows = cursor.fetchall()

    authors_map: dict[str, int] = {}
    narrators_map: dict[str, int] = {}

    for row in rows:
        book_id, author_raw, narrator_raw = row[0], row[1], row[2]

        author_entries = _process_person_names(
            author_raw, authors_map, cursor, "authors"
        )
        _link_book_persons(cursor, book_id, author_entries, "book_authors")

        narrator_entries = _process_person_names(
            narrator_raw, narrators_map, cursor, "narrators"
        )
        _link_book_persons(cursor, book_id, narrator_entries, "book_narrators")

    return len(authors_map), len(narrators_map)


def _print_dry_run_samples(cursor):
    """Print sample name parsing for dry-run preview."""
    print("=== DRY RUN ===\n")

    cursor.execute("SELECT id, author, narrator FROM audiobooks LIMIT 10")
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


def _print_current_state(cursor, db_path):
    """Print current database state statistics."""
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE author_last_name IS NOT NULL")
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

    return total, has_last, ba_count, bn_count


def _execute_and_verify(conn, cursor, has_last, ba_count, bn_count):
    """Execute the populate and rebuild, then verify results."""
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
    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE author_last_name IS NOT NULL")
    has_last_new = cursor.fetchone()[0]

    print(f"\n  book_authors rows: {ba_count} -> {ba_new}")
    print(f"  book_narrators rows: {bn_count} -> {bn_new}")
    print(f"  With author_last_name: {has_last} -> {has_last_new}")

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
    print("\nDone.")


def main():
    parser = ArgumentParser(
        description="Populate name columns and rebuild junction tables"
    )
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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    _total, has_last, ba_count, bn_count = _print_current_state(cursor, db_path)

    if not args.execute:
        _print_dry_run_samples(cursor)
        conn.close()
        return

    _execute_and_verify(conn, cursor, has_last, ba_count, bn_count)
    conn.close()


if __name__ == "__main__":
    main()
