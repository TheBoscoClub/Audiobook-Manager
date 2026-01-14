#!/usr/bin/env python3
"""
Single Directory Audiobook Importer
====================================
Imports audiobooks from a specific directory path directly to database.
Designed to be called inline by the mover script after each successful move.

Usage:
    python3 import_single.py /path/to/Library/Author/Book
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import COVER_DIR, DATABASE_PATH
# Import shared utilities
from scanner.metadata_utils import (categorize_genre, determine_literary_era,
                                    extract_cover_art, extract_topics,
                                    get_file_metadata)

SUPPORTED_FORMATS = [".m4b", ".opus", ".m4a", ".mp3"]


def get_or_create_lookup_id(cursor: sqlite3.Cursor, table: str, name: str) -> int:
    """Get or create an ID in a lookup table."""
    cursor.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,))
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError(f"Failed to insert into {table}")
    return lastrowid


def insert_audiobook(
    conn: sqlite3.Connection, metadata: dict, cover_path: str | None
) -> int | None:
    """Insert a single audiobook into the database."""
    cursor = conn.cursor()

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


def import_directory(
    dir_path: Path, db_path: Path = DATABASE_PATH, cover_dir: Path = COVER_DIR
) -> dict:
    """
    Import all audiobooks from a specific directory.

    Args:
        dir_path: Directory containing audiobook files
        db_path: Path to SQLite database
        cover_dir: Path to cover art directory

    Returns:
        dict with {added: int, skipped: int, errors: int}
    """
    added = 0
    skipped = 0
    errors = 0

    if not dir_path.is_dir():
        return {
            "added": 0,
            "skipped": 0,
            "errors": 1,
            "error": f"Not a directory: {dir_path}",
        }

    # Find audio files in this directory (recursive for nested structure)
    audio_files: list[Path] = []
    for ext in SUPPORTED_FORMATS:
        audio_files.extend(dir_path.rglob(f"*{ext}"))

    # Filter out cover art files
    audio_files = [f for f in audio_files if ".cover." not in f.name.lower()]

    if not audio_files:
        return {
            "added": 0,
            "skipped": 0,
            "errors": 0,
            "message": "No audio files found",
        }

    # Check which files are already in DB
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    existing = set()
    for f in audio_files:
        cursor.execute("SELECT 1 FROM audiobooks WHERE file_path = ?", (str(f),))
        if cursor.fetchone():
            existing.add(str(f))

    new_files = [f for f in audio_files if str(f) not in existing]
    skipped = len(existing)

    if not new_files:
        conn.close()
        return {"added": 0, "skipped": skipped, "errors": 0}

    # Ensure cover directory exists
    cover_dir.mkdir(parents=True, exist_ok=True)

    try:
        for filepath in new_files:
            # Extract metadata (skip hash for speed - mover already validated)
            metadata = get_file_metadata(
                filepath, audiobook_dir=dir_path.parent, calculate_hash=False
            )
            if not metadata:
                errors += 1
                continue

            # Extract cover art
            cover_path = extract_cover_art(filepath, cover_dir)

            try:
                insert_audiobook(conn, metadata, cover_path)
                conn.commit()
                added += 1
                print(
                    f"✓ Imported: {metadata.get('title')} by {metadata.get('author')}"
                )
            except sqlite3.IntegrityError:
                skipped += 1
                conn.rollback()
            except Exception as e:
                print(f"✗ Error: {e}", file=sys.stderr)
                errors += 1
                conn.rollback()
    finally:
        conn.close()

    return {"added": added, "skipped": skipped, "errors": errors}


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory_path>", file=sys.stderr)
        sys.exit(1)

    dir_path = Path(sys.argv[1])

    if not dir_path.exists():
        print(f"Path does not exist: {dir_path}", file=sys.stderr)
        sys.exit(1)

    result = import_directory(dir_path)

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Import complete: {result['added']} added, {result['skipped']} skipped, {result['errors']} errors"
    )
    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
