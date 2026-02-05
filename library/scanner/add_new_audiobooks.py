#!/usr/bin/env python3
"""
Incremental Audiobook Adder
============================
Scans library for audiobooks NOT already in the database and adds them directly.
This is much faster than a full rescan for large libraries.

Unlike scan_audiobooks.py which:
1. Scans ALL files
2. Writes to JSON
3. Requires separate import step

This script:
1. Queries DB for existing file paths
2. Scans library for new files only
3. Inserts directly into SQLite
"""

import sqlite3
import sys
from pathlib import Path
from typing import Callable, Optional

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOK_DIR, COVER_DIR, DATABASE_PATH
# Import shared utilities from scanner package
from scanner.metadata_utils import (categorize_genre, determine_literary_era,
                                    extract_cover_art, extract_topics,
                                    get_file_metadata)

SUPPORTED_FORMATS = [".m4b", ".opus", ".m4a", ".mp3"]

# Progress callback type
ProgressCallback = Optional[Callable[[int, int, str], None]]


def get_existing_paths(db_path: Path) -> set[str]:
    """Get all file paths already in the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM audiobooks")
    paths = {row[0] for row in cursor.fetchall()}
    conn.close()
    return paths


def find_new_audiobooks(library_dir: Path, existing_paths: set[str]) -> list[Path]:
    """Find audiobook files not already in the database."""
    all_files = []

    for ext in SUPPORTED_FORMATS:
        files = list(library_dir.rglob(f"*{ext}"))
        all_files.extend(files)

    # Filter out cover art files
    all_files = [f for f in all_files if ".cover." not in f.name.lower()]

    # Deduplicate: prefer main Library over /Library/Audiobook/
    main_files = [f for f in all_files if "/Library/Audiobook/" not in str(f)]
    audiobook_files = [f for f in all_files if "/Library/Audiobook/" in str(f)]
    main_stems = {f.stem for f in main_files}
    unique_audiobook = [f for f in audiobook_files if f.stem not in main_stems]
    all_files = main_files + unique_audiobook

    # Filter to only NEW files (not in database)
    new_files = [f for f in all_files if str(f) not in existing_paths]

    return new_files


# Whitelist of allowed lookup tables for SQL queries - prevents SQL injection
ALLOWED_LOOKUP_TABLES = frozenset({"genres", "eras", "topics"})


def get_or_create_lookup_id(cursor: sqlite3.Cursor, table: str, name: str) -> int:
    """Get or create an ID in a lookup table (genres, eras, topics).

    Args:
        cursor: Database cursor
        table: Table name - MUST be one of: genres, eras, topics
        name: Value to insert/lookup

    Raises:
        ValueError: If table name is not in the whitelist
    """
    # SQL injection prevention: validate table name against whitelist
    if table not in ALLOWED_LOOKUP_TABLES:
        raise ValueError(
            f"Invalid table name: {table}. Must be one of: {ALLOWED_LOOKUP_TABLES}"
        )

    cursor.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))  # nosec B608 - table validated above
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,))  # nosec B608 - table validated above
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError(f"Failed to insert into {table}")
    return lastrowid


def insert_audiobook(
    conn: sqlite3.Connection, metadata: dict, cover_path: Optional[str]
) -> Optional[int]:
    """Insert a single audiobook into the database. Returns the new ID."""
    cursor = conn.cursor()

    # Insert main record
    cursor.execute(
        """
        INSERT INTO audiobooks (
            title, author, narrator, publisher, series,
            duration_hours, duration_formatted, file_size_mb,
            file_path, cover_path, format, description,
            sha256_hash, hash_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            metadata.get("title"),
            metadata.get("author"),
            metadata.get("narrator"),
            metadata.get("publisher"),
            metadata.get("series"),
            metadata.get("duration_hours"),
            metadata.get("duration_formatted"),
            metadata.get("file_size_mb"),
            metadata.get("file_path"),
            cover_path,
            metadata.get("format"),
            metadata.get("description", ""),
            metadata.get("sha256_hash"),
            metadata.get("hash_verified_at"),
        ),
    )

    audiobook_id = cursor.lastrowid

    # Insert genre
    genre = metadata.get("genre", "Uncategorized")
    genre_cat = categorize_genre(genre)
    genre_id = get_or_create_lookup_id(cursor, "genres", genre_cat["sub"])
    cursor.execute(
        "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
        (audiobook_id, genre_id),
    )

    # Insert era
    era = determine_literary_era(metadata.get("year", ""))
    era_id = get_or_create_lookup_id(cursor, "eras", era)
    cursor.execute(
        "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)",
        (audiobook_id, era_id),
    )

    # Insert topics
    topics = extract_topics(metadata.get("description", ""))
    for topic_name in topics:
        topic_id = get_or_create_lookup_id(cursor, "topics", topic_name)
        cursor.execute(
            "INSERT INTO audiobook_topics (audiobook_id, topic_id) VALUES (?, ?)",
            (audiobook_id, topic_id),
        )

    return audiobook_id


def add_new_audiobooks(
    library_dir: Path = AUDIOBOOK_DIR,
    db_path: Path = DATABASE_PATH,
    cover_dir: Path = COVER_DIR,
    calculate_hashes: bool = True,
    progress_callback: ProgressCallback = None,
) -> dict:
    """
    Find and add new audiobooks to the database.

    Args:
        library_dir: Path to audiobook library
        db_path: Path to SQLite database
        cover_dir: Path to cover art directory
        calculate_hashes: Whether to calculate SHA-256 hashes
        progress_callback: Optional callback(current, total, message)

    Returns:
        dict with results: {added: int, skipped: int, errors: int, new_files: list}
    """
    added_count = 0
    skipped_count = 0
    errors_count = 0
    new_files_list: list[dict] = []

    # Get existing paths
    if progress_callback:
        progress_callback(0, 100, "Querying database for existing files...")

    existing_paths = get_existing_paths(db_path)
    print(f"Found {len(existing_paths)} existing audiobooks in database")

    # Find new files
    if progress_callback:
        progress_callback(5, 100, "Scanning library for new files...")

    new_files = find_new_audiobooks(library_dir, existing_paths)
    print(f"Found {len(new_files)} new audiobooks to add")

    if not new_files:
        if progress_callback:
            progress_callback(100, 100, "No new audiobooks found")
        return {
            "added": added_count,
            "skipped": skipped_count,
            "errors": errors_count,
            "new_files": new_files_list,
        }

    # Ensure cover directory exists
    cover_dir.mkdir(parents=True, exist_ok=True)

    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        total = len(new_files)
        for idx, filepath in enumerate(new_files, 1):
            # Calculate progress (5-95% range for processing)
            pct = 5 + int((idx / total) * 90)

            if progress_callback:
                progress_callback(
                    pct, 100, f"Processing {idx}/{total}: {filepath.name}"
                )

            print(f"[{idx:3d}/{total}] Adding: {filepath.name}")

            # Extract metadata using shared utility
            metadata = get_file_metadata(
                filepath, audiobook_dir=library_dir, calculate_hash=calculate_hashes
            )
            if not metadata:
                errors_count += 1
                continue

            # Extract cover art
            cover_path = extract_cover_art(filepath, cover_dir)

            try:
                # Insert into database
                audiobook_id = insert_audiobook(conn, metadata, cover_path)
                conn.commit()

                added_count += 1
                new_files_list.append(
                    {
                        "id": audiobook_id,
                        "title": metadata.get("title"),
                        "author": metadata.get("author"),
                        "file_path": str(filepath),
                    }
                )

            except sqlite3.IntegrityError:
                # File path already exists (race condition or duplicate)
                print(f"  Skipped (already exists): {filepath.name}")
                skipped_count += 1
                conn.rollback()
            except Exception as e:
                print(f"  Error inserting: {e}")
                errors_count += 1
                conn.rollback()

        if progress_callback:
            progress_callback(100, 100, f"Complete: Added {added_count} audiobooks")

    finally:
        conn.close()

    return {
        "added": added_count,
        "skipped": skipped_count,
        "errors": errors_count,
        "new_files": new_files_list,
    }


def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Add new audiobooks to database (incremental scan)"
    )
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="Skip SHA-256 hash calculation (faster but no integrity verification)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without actually adding",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("INCREMENTAL AUDIOBOOK SCANNER")
    print("=" * 60)
    print(f"Library:  {AUDIOBOOK_DIR}")
    print(f"Database: {DATABASE_PATH}")
    print(f"Covers:   {COVER_DIR}")
    print()

    if args.dry_run:
        # Just show what would be added
        existing = get_existing_paths(DATABASE_PATH)
        new_files = find_new_audiobooks(AUDIOBOOK_DIR, existing)

        print(f"Would add {len(new_files)} new audiobooks:")
        for f in new_files[:20]:
            print(f"  - {f.name}")
        if len(new_files) > 20:
            print(f"  ... and {len(new_files) - 20} more")
        return

    # Run the incremental add
    results = add_new_audiobooks(calculate_hashes=not args.no_hash)

    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Added:   {results['added']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Errors:  {results['errors']}")

    if results["new_files"]:
        print()
        print("New audiobooks added:")
        for book in results["new_files"][:10]:
            print(f"  - {book['title']} by {book['author']}")
        if len(results["new_files"]) > 10:
            print(f"  ... and {len(results['new_files']) - 10} more")


if __name__ == "__main__":
    main()
