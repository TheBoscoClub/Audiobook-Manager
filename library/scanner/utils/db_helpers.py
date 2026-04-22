"""
Shared database helper functions for inserting audiobooks and managing
lookup tables (genres, eras, topics).

Used by add_new_audiobooks and import_single to avoid duplicated SQL logic.
"""

import sqlite3
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for metadata_utils import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scanner.metadata_utils import categorize_genre, determine_literary_era, extract_topics

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
        raise ValueError(f"Invalid table name: {table}. Must be one of: {ALLOWED_LOOKUP_TABLES}")

    cursor.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"SELECT id FROM {table} WHERE name = ?",  # nosec B608 — table validated against ALLOWED_LOOKUP_TABLES allowlist at L33; name is parameter-bound
        (name,),  # noqa: S608
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        f"INSERT INTO {table} (name) VALUES (?)",  # nosec B608 — table validated against ALLOWED_LOOKUP_TABLES allowlist at L33; name is parameter-bound
        (name,),  # noqa: S608
    )
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
            duration_hours, duration_formatted, chapter_count, file_size_mb,
            file_path, cover_path, format, description,
            sha256_hash, hash_verified_at, asin,
            published_year, published_date, acquired_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            metadata.get("title"),
            metadata.get("author"),
            metadata.get("narrator"),
            metadata.get("publisher"),
            metadata.get("series"),
            metadata.get("duration_hours"),
            metadata.get("duration_formatted"),
            metadata.get("chapter_count"),
            metadata.get("file_size_mb"),
            metadata.get("file_path"),
            cover_path,
            metadata.get("format"),
            metadata.get("description", ""),
            metadata.get("sha256_hash"),
            metadata.get("hash_verified_at"),
            metadata.get("asin"),
            metadata.get("published_year"),
            metadata.get("published_date"),
            metadata.get("acquired_date"),
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
        "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)", (audiobook_id, era_id)
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
