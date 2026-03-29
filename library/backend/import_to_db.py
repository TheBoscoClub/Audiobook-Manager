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
from config import COVER_DIR, DATA_DIR, DATABASE_PATH

from name_parser import (
    clean_name,
    generate_sort_name,
    is_brand_name,
    is_junk_name,
    normalize_for_dedup,
    parse_names,
)

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


def _cleanup_orphaned_covers(cursor):
    """Remove cover files from COVER_DIR that are not referenced by any audiobook.

    After a rescan/reimport, cover filenames change (MD5 of filepath). Old cover
    files become orphans consuming disk space. This deletes any file in COVER_DIR
    that no audiobook row references.
    """
    if not COVER_DIR.is_dir():
        return

    cursor.execute(
        "SELECT cover_path FROM audiobooks "
        "WHERE cover_path IS NOT NULL AND cover_path != ''"
    )
    referenced = {row[0] for row in cursor.fetchall()}

    on_disk = set()
    for f in COVER_DIR.iterdir():
        if f.is_file():
            on_disk.add(f.name)

    orphans = on_disk - referenced
    if not orphans:
        print("\n✓ No orphaned cover files")
        return

    total_bytes = 0
    for name in orphans:
        path = COVER_DIR / name
        total_bytes += path.stat().st_size
        path.unlink()

    mb = total_bytes / (1024 * 1024)
    print(f"\n✓ Cleaned up {len(orphans)} orphaned cover files ({mb:.1f} MB)")


def _populate_names_and_junctions(cursor):
    """Populate author/narrator name columns and rebuild junction tables.

    Parses the flat author/narrator fields into structured data:
    - Sets author_last_name, author_first_name, narrator_last_name, narrator_first_name
    - Rebuilds authors/narrators normalized tables
    - Rebuilds book_authors/book_narrators junction tables
    """
    cursor.execute("SELECT id, author, narrator FROM audiobooks")
    rows = cursor.fetchall()

    authors_map: dict[str, int] = {}  # dedup_key -> author_id
    narrators_map: dict[str, int] = {}  # dedup_key -> narrator_id

    for row in rows:
        book_id, author_raw, narrator_raw = row[0], row[1], row[2]

        # Populate name-split columns from primary (first) name
        author_first = author_last = narrator_first = narrator_last = None

        if author_raw and author_raw.strip():
            a_names = parse_names(author_raw)
            if a_names:
                sort_name = generate_sort_name(a_names[0])
                if sort_name and ", " in sort_name:
                    parts = sort_name.split(", ", 1)
                    author_last, author_first = parts[0], parts[1]
                elif sort_name:
                    author_last = sort_name

                # Build junction rows for ALL authors
                for position, name in enumerate(a_names):
                    if is_junk_name(name) or is_brand_name(name):
                        continue
                    cleaned = clean_name(name)
                    if not cleaned:
                        continue
                    dedup_key = normalize_for_dedup(cleaned)
                    if dedup_key not in authors_map:
                        sn = generate_sort_name(cleaned) or cleaned
                        cursor.execute(
                            "INSERT INTO authors (name, sort_name) VALUES (?, ?)",
                            (cleaned, sn),
                        )
                        authors_map[dedup_key] = cursor.lastrowid
                    try:
                        cursor.execute(
                            "INSERT INTO book_authors (book_id, author_id, position) "
                            "VALUES (?, ?, ?)",
                            (book_id, authors_map[dedup_key], position),
                        )
                    except Exception:
                        pass  # Duplicate junction row

        if narrator_raw and narrator_raw.strip():
            n_names = parse_names(narrator_raw)
            if n_names:
                sort_name = generate_sort_name(n_names[0])
                if sort_name and ", " in sort_name:
                    parts = sort_name.split(", ", 1)
                    narrator_last, narrator_first = parts[0], parts[1]
                elif sort_name:
                    narrator_last = sort_name

                for position, name in enumerate(n_names):
                    if is_junk_name(name) or is_brand_name(name):
                        continue
                    cleaned = clean_name(name)
                    if not cleaned:
                        continue
                    dedup_key = normalize_for_dedup(cleaned)
                    if dedup_key not in narrators_map:
                        sn = generate_sort_name(cleaned) or cleaned
                        cursor.execute(
                            "INSERT INTO narrators (name, sort_name) VALUES (?, ?)",
                            (cleaned, sn),
                        )
                        narrators_map[dedup_key] = cursor.lastrowid
                    try:
                        cursor.execute(
                            "INSERT INTO book_narrators (book_id, narrator_id, position) "
                            "VALUES (?, ?, ?)",
                            (book_id, narrators_map[dedup_key], position),
                        )
                    except Exception:
                        pass

        cursor.execute(
            "UPDATE audiobooks SET author_last_name=?, author_first_name=?, "
            "narrator_last_name=?, narrator_first_name=? WHERE id=?",
            (author_last, author_first, narrator_last, narrator_first, book_id),
        )

    print(f"✓ Populated name columns for {len(rows)} audiobooks")
    print(
        f"✓ Created {len(authors_map)} unique authors, {len(narrators_map)} unique narrators"
    )

    # Stats
    cursor.execute("SELECT COUNT(*) FROM book_authors")
    ba = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM book_narrators")
    bn = cursor.fetchone()[0]
    print(f"✓ book_authors: {ba} rows, book_narrators: {bn} rows")


def import_audiobooks(conn):
    """Import audiobooks from JSON, preserving manually-populated metadata"""
    print(f"\nLoading audiobooks from: {JSON_PATH}")

    with open(JSON_PATH) as f:
        data = json.load(f)

    audiobooks = data["audiobooks"]
    print(f"Found {len(audiobooks)} audiobooks")

    cursor = conn.cursor()

    # PRESERVE existing metadata that was populated from external sources
    # (Audible API enrichment, ISBN enrichment, Audible export, etc.)
    # These would be lost on reimport since the JSON only has scanner data
    print("\nPreserving existing metadata...")

    # Save content_type for ALL entries where it differs from default 'Product'.
    # This captures values set by populate_content_types.py (which does NOT set
    # audible_enriched_at), ensuring they survive reimport.
    preserved_content_types = {}
    cursor.execute(
        "SELECT file_path, content_type FROM audiobooks"
        " WHERE content_type IS NOT NULL AND content_type != 'Product'"
    )
    for row in cursor.fetchall():
        preserved_content_types[row[0]] = row[1]
    print(
        f"  Preserved {len(preserved_content_types)} non-default content_type records"
    )

    # Save narrator data (keyed by file_path)
    preserved_narrators = {}
    cursor.execute(
        "SELECT file_path, narrator FROM audiobooks"
        " WHERE narrator IS NOT NULL"
        " AND narrator != 'Unknown Narrator' AND narrator != ''"
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

    # Save Audible enrichment data (keyed by file_path)
    # This captures ALL fields populated by enrich_from_audible.py
    preserved_enrichment = {}
    cursor.execute("""
        SELECT file_path, series, series_sequence, subtitle, language,
               format_type, runtime_length_min, release_date,
               publisher_summary, rating_overall, rating_performance,
               rating_story, num_ratings, num_reviews, audible_image_url,
               sample_url, audible_sku, is_adult_product,
               merchandising_summary, audible_enriched_at, isbn_enriched_at,
               content_type
        FROM audiobooks
        WHERE audible_enriched_at IS NOT NULL OR isbn_enriched_at IS NOT NULL
    """)
    for row in cursor.fetchall():
        preserved_enrichment[row[0]] = {
            "series": row[1],
            "series_sequence": row[2],
            "subtitle": row[3],
            "language": row[4],
            "format_type": row[5],
            "runtime_length_min": row[6],
            "release_date": row[7],
            "publisher_summary": row[8],
            "rating_overall": row[9],
            "rating_performance": row[10],
            "rating_story": row[11],
            "num_ratings": row[12],
            "num_reviews": row[13],
            "audible_image_url": row[14],
            "sample_url": row[15],
            "audible_sku": row[16],
            "is_adult_product": row[17],
            "merchandising_summary": row[18],
            "audible_enriched_at": row[19],
            "isbn_enriched_at": row[20],
            "content_type": row[21],
        }
    print(f"  Preserved enrichment data for {len(preserved_enrichment)} audiobooks")

    # Save Audible categories (keyed by file_path)
    preserved_categories = {}
    cursor.execute("""
        SELECT a.file_path, ac.category_path, ac.category_name,
               ac.root_category, ac.depth, ac.audible_category_id
        FROM audiobooks a
        JOIN audible_categories ac ON a.id = ac.audiobook_id
    """)
    for row in cursor.fetchall():
        fp = row[0]
        if fp not in preserved_categories:
            preserved_categories[fp] = []
        preserved_categories[fp].append(
            {
                "category_path": row[1],
                "category_name": row[2],
                "root_category": row[3],
                "depth": row[4],
                "audible_category_id": row[5],
            }
        )
    print(f"  Preserved categories for {len(preserved_categories)} audiobooks")

    # Save editorial reviews (keyed by file_path)
    preserved_reviews = {}
    cursor.execute("""
        SELECT a.file_path, er.review_text, er.source
        FROM audiobooks a
        JOIN editorial_reviews er ON a.id = er.audiobook_id
    """)
    for row in cursor.fetchall():
        fp = row[0]
        if fp not in preserved_reviews:
            preserved_reviews[fp] = []
        preserved_reviews[fp].append(
            {
                "review_text": row[1],
                "source": row[2],
            }
        )
    print(f"  Preserved editorial reviews for {len(preserved_reviews)} audiobooks")

    # Clear existing data
    cursor.execute("DELETE FROM audiobook_topics")
    cursor.execute("DELETE FROM audiobook_eras")
    cursor.execute("DELETE FROM audiobook_genres")
    cursor.execute("DELETE FROM audible_categories")
    cursor.execute("DELETE FROM editorial_reviews")
    cursor.execute("DELETE FROM book_authors")
    cursor.execute("DELETE FROM book_narrators")
    cursor.execute("DELETE FROM audiobooks")
    cursor.execute("DELETE FROM topics")
    cursor.execute("DELETE FROM eras")
    cursor.execute("DELETE FROM genres")
    cursor.execute("DELETE FROM authors")
    cursor.execute("DELETE FROM narrators")

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
                sha256_hash, hash_verified_at, asin,
                published_year, published_date, acquired_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                book.get("asin"),
                book.get("published_year"),
                book.get("published_date"),
                book.get("acquired_date"),
            ),
        )

        audiobook_id = cursor.lastrowid

        # Restore enrichment data if available for this file
        enrichment = preserved_enrichment.get(file_path)
        if enrichment:
            # Whitelist of valid enrichment columns to prevent SQL injection
            allowed_columns = {
                "series",
                "series_sequence",
                "subtitle",
                "language",
                "format_type",
                "runtime_length_min",
                "release_date",
                "publisher_summary",
                "rating_overall",
                "rating_performance",
                "rating_story",
                "num_ratings",
                "num_reviews",
                "audible_image_url",
                "sample_url",
                "audible_sku",
                "is_adult_product",
                "merchandising_summary",
                "audible_enriched_at",
                "isbn_enriched_at",
                "content_type",
            }
            enrich_updates = []
            enrich_params = []
            for col, val in enrichment.items():
                if val is not None and col in allowed_columns:
                    enrich_updates.append(f"{col} = ?")
                    enrich_params.append(val)
            if enrich_updates:
                enrich_params.append(audiobook_id)
                cursor.execute(
                    f"UPDATE audiobooks SET {', '.join(enrich_updates)} WHERE id = ?",  # nosec B608 — column names whitelisted above
                    enrich_params,
                )

        # Restore content_type for non-enriched entries (e.g. set by
        # populate_content_types.py which doesn't set audible_enriched_at).
        # Only applies if enrichment didn't already restore content_type.
        if not enrichment and file_path in preserved_content_types:
            cursor.execute(
                "UPDATE audiobooks SET content_type = ? WHERE id = ?",
                (preserved_content_types[file_path], audiobook_id),
            )

        # Restore Audible categories
        cats = preserved_categories.get(file_path, [])
        for cat in cats:
            cursor.execute(
                "INSERT INTO audible_categories "
                "(audiobook_id, category_path, category_name, "
                "root_category, depth, audible_category_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    audiobook_id,
                    cat["category_path"],
                    cat["category_name"],
                    cat["root_category"],
                    cat["depth"],
                    cat["audible_category_id"],
                ),
            )

        # Restore editorial reviews
        revs = preserved_reviews.get(file_path, [])
        for rev in revs:
            cursor.execute(
                "INSERT INTO editorial_reviews "
                "(audiobook_id, review_text, source) VALUES (?, ?, ?)",
                (audiobook_id, rev["review_text"], rev["source"]),
            )

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
    print(f"✓ Restored enrichment for {len(preserved_enrichment)} audiobooks")
    print(f"✓ Restored categories for {len(preserved_categories)} audiobooks")
    print(f"✓ Restored reviews for {len(preserved_reviews)} audiobooks")
    print(f"✓ Total {len(genres_map)} unique genres")
    print(f"✓ Imported {len(eras_map)} eras")
    print(f"✓ Imported {len(topics_map)} topics")

    # Populate name-split columns and rebuild junction tables
    print("\nPopulating name columns and junction tables...")
    _populate_names_and_junctions(cursor)
    conn.commit()

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

    cursor.execute(
        "SELECT COUNT(*) FROM audiobooks WHERE asin IS NOT NULL AND asin <> ''"
    )
    asin_count = cursor.fetchone()[0]

    print("\n=== Database Statistics ===")
    print(f"Total audiobooks: {total:,}")
    print(f"Total hours: {int(total_hours):,} ({int(total_hours / 24):,} days)")
    print(f"Unique authors: {unique_authors}")
    print(f"Unique narrators: {unique_narrators}")
    print(f"Unique genres: {len(genres_map)}")
    print(f"With SHA-256 hashes: {hashed_count:,}")
    print(f"With ASINs: {asin_count:,}")

    # Clean up orphaned cover files
    _cleanup_orphaned_covers(cursor)

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
    test_titles = [
        b.get("title", "") for b in audiobooks if "Test Audiobook" in b.get("title", "")
    ]
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
