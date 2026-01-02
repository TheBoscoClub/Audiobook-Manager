#!/usr/bin/env python3
"""
Import audiobooks from JSON into SQLite database
Builds indices for fast querying
"""

import json
import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH, DATA_DIR

DB_PATH = DATABASE_PATH
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
JSON_PATH = DATA_DIR / "audiobooks.json"


def create_database():
    """Create database with schema"""
    print(f"Creating database: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Load and execute schema
    with open(SCHEMA_PATH) as f:
        schema = f.read()

    cursor.executescript(schema)
    conn.commit()

    print("✓ Database schema created")
    return conn


def import_audiobooks(conn):
    """Import audiobooks from JSON, preserving manually-populated metadata"""
    print(f"\nLoading audiobooks from: {JSON_PATH}")

    with open(JSON_PATH) as f:
        data = json.load(f)

    audiobooks = data["audiobooks"]
    print(f"Found {len(audiobooks)} audiobooks")

    cursor = conn.cursor()

    # PRESERVE existing narrator and genre data before clearing
    # These are populated from Audible export and would be lost on reimport
    print("\nPreserving existing metadata...")

    # Save narrator data (keyed by file_path)
    preserved_narrators = {}
    cursor.execute(
        "SELECT file_path, narrator FROM audiobooks WHERE narrator IS NOT NULL AND narrator != 'Unknown Narrator' AND narrator != ''"
    )
    for row in cursor.fetchall():
        preserved_narrators[row[0]] = row[1]
    print(f"  Preserved {len(preserved_narrators)} narrator records")

    # Save genre data (keyed by file_path)
    preserved_genres = {}
    cursor.execute("""
        SELECT a.file_path, GROUP_CONCAT(g.name, '|||')
        FROM audiobooks a
        JOIN audiobook_genres ag ON a.id = ag.audiobook_id
        JOIN genres g ON ag.genre_id = g.id
        GROUP BY a.file_path
    """)
    for row in cursor.fetchall():
        if row[1]:
            preserved_genres[row[0]] = row[1].split("|||")
    print(f"  Preserved genre data for {len(preserved_genres)} audiobooks")

    # Clear existing data
    cursor.execute("DELETE FROM audiobook_topics")
    cursor.execute("DELETE FROM audiobook_eras")
    cursor.execute("DELETE FROM audiobook_genres")
    cursor.execute("DELETE FROM audiobooks")
    cursor.execute("DELETE FROM topics")
    cursor.execute("DELETE FROM eras")
    cursor.execute("DELETE FROM genres")

    print("\nImporting audiobooks...")

    # Track unique values
    genres_map = {}
    eras_map = {}
    topics_map = {}

    for idx, book in enumerate(audiobooks, 1):
        if idx % 100 == 0:
            print(f"  Processed {idx}/{len(audiobooks)} audiobooks...")

        # Use preserved narrator if available, otherwise use JSON value
        file_path = book.get("file_path")
        narrator = preserved_narrators.get(file_path, book.get("narrator"))

        # Insert audiobook
        cursor.execute(
            """
            INSERT INTO audiobooks (
                title, author, narrator, publisher, series,
                duration_hours, duration_formatted, file_size_mb,
                file_path, cover_path, format, quality, description,
                sha256_hash, hash_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                book.get("title"),
                book.get("author"),
                narrator,
                book.get("publisher"),
                book.get("series"),
                book.get("duration_hours"),
                book.get("duration_formatted"),
                book.get("file_size_mb"),
                file_path,
                book.get("cover_path"),
                book.get("format"),
                book.get("quality"),
                book.get("description", ""),
                book.get("sha256_hash"),
                book.get("hash_verified_at"),
            ),
        )

        audiobook_id = cursor.lastrowid

        # Handle genres - use preserved genres if available, otherwise use JSON
        genre_list = preserved_genres.get(file_path, book.get("genres", []))
        for genre_name in genre_list:
            if genre_name not in genres_map:
                cursor.execute("INSERT INTO genres (name) VALUES (?)", (genre_name,))
                genres_map[genre_name] = cursor.lastrowid

            cursor.execute(
                "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
                (audiobook_id, genres_map[genre_name]),
            )

        # Handle eras
        for era_name in book.get("eras", []):
            if era_name not in eras_map:
                cursor.execute("INSERT INTO eras (name) VALUES (?)", (era_name,))
                eras_map[era_name] = cursor.lastrowid

            cursor.execute(
                "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)",
                (audiobook_id, eras_map[era_name]),
            )

        # Handle topics
        for topic_name in book.get("topics", []):
            if topic_name not in topics_map:
                cursor.execute("INSERT INTO topics (name) VALUES (?)", (topic_name,))
                topics_map[topic_name] = cursor.lastrowid

            cursor.execute(
                "INSERT INTO audiobook_topics (audiobook_id, topic_id) VALUES (?, ?)",
                (audiobook_id, topics_map[topic_name]),
            )

    conn.commit()

    print(f"\n✓ Imported {len(audiobooks)} audiobooks")
    print(f"✓ Restored {len(preserved_narrators)} narrator records")
    print(f"✓ Restored genres for {len(preserved_genres)} audiobooks")
    print(f"✓ Total {len(genres_map)} unique genres")
    print(f"✓ Imported {len(eras_map)} eras")
    print(f"✓ Imported {len(topics_map)} topics")

    # Show statistics
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(duration_hours) FROM audiobooks")
    total_hours = cursor.fetchone()[0] or 0

    cursor.execute(
        "SELECT COUNT(DISTINCT author) FROM audiobooks WHERE author IS NOT NULL"
    )
    unique_authors = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(DISTINCT narrator) FROM audiobooks WHERE narrator IS NOT NULL"
    )
    unique_narrators = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE sha256_hash IS NOT NULL")
    hashed_count = cursor.fetchone()[0]

    print("\n=== Database Statistics ===")
    print(f"Total audiobooks: {total:,}")
    print(f"Total hours: {int(total_hours):,} ({int(total_hours / 24):,} days)")
    print(f"Unique authors: {unique_authors}")
    print(f"Unique narrators: {unique_narrators}")
    print(f"Unique genres: {len(genres_map)}")
    print(f"With SHA-256 hashes: {hashed_count:,}")

    # Optimize database
    print("\nOptimizing database...")
    cursor.execute("VACUUM")
    cursor.execute("ANALYZE")
    print("✓ Database optimized")


def validate_json_source(json_path: Path) -> bool:
    """
    Validate that the JSON source is production data, not test fixtures.
    Returns True if safe to import, exits with error if test data detected.
    """
    with open(json_path) as f:
        data = json.load(f)

    audiobooks = data.get("audiobooks", [])

    # Safety check 1: Very few audiobooks might indicate test data
    if len(audiobooks) < 20:
        print(f"\n⚠️  WARNING: JSON file contains only {len(audiobooks)} audiobooks!")
        print(f"   Source: {json_path}")
        print("   This looks like test data, not a production library.")
        print("\n   If this is intentional, set SKIP_IMPORT_VALIDATION=1")
        print("   If not, ensure DATA_DIR points to production data.\n")

        import os
        if os.environ.get("SKIP_IMPORT_VALIDATION") != "1":
            sys.exit(1)

    # Safety check 2: Test audiobook titles
    test_titles = [b.get("title", "") for b in audiobooks if "Test Audiobook" in b.get("title", "")]
    if test_titles:
        print("\n⚠️  WARNING: JSON file contains test audiobook titles!")
        print(f"   Found: {test_titles[:5]}")
        print(f"   Source: {json_path}")
        print("\n   This is test data and should NOT be imported to production.")
        print("   If this is intentional, set SKIP_IMPORT_VALIDATION=1\n")

        import os
        if os.environ.get("SKIP_IMPORT_VALIDATION") != "1":
            sys.exit(1)

    return True


def main():
    """Main import process"""
    if not JSON_PATH.exists():
        print(f"Error: JSON file not found: {JSON_PATH}")
        print("Please run the scanner first: python3 scanner/scan_audiobooks.py")
        sys.exit(1)

    # Validate JSON source before importing
    validate_json_source(JSON_PATH)

    conn = create_database()

    try:
        import_audiobooks(conn)
        print(f"\n✓ Database created successfully: {DB_PATH}")
        print(f"  Size: {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
