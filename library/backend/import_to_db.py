#!/usr/bin/env python3
"""
Import audiobooks from JSON into SQLite database
Builds indices for fast querying
"""

import json
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from name_parser import (  # noqa: E402
    clean_name,
    generate_sort_name,
    is_brand_name,
    is_junk_name,
    normalize_for_dedup,
    parse_names,
)

from config import COVER_DIR, DATA_DIR, DATABASE_PATH  # noqa: E402

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
        "SELECT cover_path FROM audiobooks WHERE cover_path IS NOT NULL AND cover_path != ''"
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


def _split_sort_name(sort_name):
    """Split a 'Last, First' sort name into (last, first) tuple."""
    if sort_name and ", " in sort_name:
        parts = sort_name.split(", ", 1)
        return parts[0], parts[1]
    return sort_name, None


def _extract_name_columns(raw_name, parse_fn):
    """Extract first/last name columns from a raw name string.

    Returns (last_name, first_name) or (None, None) if empty.
    """
    if not raw_name or not raw_name.strip():
        return None, None
    names = parse_fn(raw_name)
    if not names:
        return None, None
    sort_name = generate_sort_name(names[0])
    return _split_sort_name(sort_name)


def _ensure_entity(cursor, cleaned, entity_map, table):
    """Ensure an entity exists in the table and return its ID."""
    dedup_key = normalize_for_dedup(cleaned)
    if dedup_key not in entity_map:
        sn = generate_sort_name(cleaned) or cleaned
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"INSERT INTO {table} (name, sort_name) VALUES (?, ?)", (cleaned, sn)  # nosec B608
        )
        entity_map[dedup_key] = cursor.lastrowid
    return entity_map[dedup_key]


def _link_entity(cursor, book_id, entity_id, position, junction_table, id_col):
    """Insert a junction table row, ignoring duplicates."""
    try:
        cursor.execute(
            f"INSERT INTO {junction_table} (book_id, {id_col}, position) "  # nosec B608
            "VALUES (?, ?, ?)",
            (book_id, entity_id, position),
        )
    except Exception as e:
        logger.debug("junction row insert (non-fatal duplicate): %s", e)


def _insert_entity_junctions(cursor, book_id, raw_name, entity_map, table, id_col):
    """Insert normalized entity rows and junction table entries."""
    if not raw_name or not raw_name.strip():
        return
    names = parse_names(raw_name)
    if not names:
        return

    junction_table = f"book_{table}"
    for position, name in enumerate(names):
        if is_junk_name(name) or is_brand_name(name):
            continue
        cleaned = clean_name(name)
        if not cleaned:
            continue
        entity_id = _ensure_entity(cursor, cleaned, entity_map, table)
        _link_entity(cursor, book_id, entity_id, position, junction_table, id_col)


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
        author_last, author_first = _extract_name_columns(author_raw, parse_names)
        narrator_last, narrator_first = _extract_name_columns(narrator_raw, parse_names)

        # Build junction rows for ALL authors and narrators
        _insert_entity_junctions(cursor, book_id, author_raw, authors_map, "authors", "author_id")
        _insert_entity_junctions(
            cursor, book_id, narrator_raw, narrators_map, "narrators", "narrator_id"
        )

        cursor.execute(
            "UPDATE audiobooks SET author_last_name=?, author_first_name=?, "
            "narrator_last_name=?, narrator_first_name=? WHERE id=?",
            (author_last, author_first, narrator_last, narrator_first, book_id),
        )

    print(f"✓ Populated name columns for {len(rows)} audiobooks")
    print(f"✓ Created {len(authors_map)} unique authors, {len(narrators_map)} unique narrators")

    # Stats
    cursor.execute("SELECT COUNT(*) FROM book_authors")
    ba = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM book_narrators")
    bn = cursor.fetchone()[0]
    print(f"✓ book_authors: {ba} rows, book_narrators: {bn} rows")


# ---------- Enrichment preservation helpers ----------

# Whitelist of valid enrichment columns to prevent SQL injection
_ENRICHMENT_COLUMNS = frozenset(
    {
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
)

_ENRICHMENT_FIELDS = [
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
]


def _preserve_content_types(cursor):
    """Preserve non-default content_type records."""
    preserved = {}
    cursor.execute(
        "SELECT file_path, content_type FROM audiobooks"
        " WHERE content_type IS NOT NULL AND content_type != 'Product'"
    )
    for row in cursor.fetchall():
        preserved[row[0]] = row[1]
    print(f"  Preserved {len(preserved)} non-default content_type records")
    return preserved


def _preserve_narrators(cursor):
    """Preserve narrator data keyed by file_path."""
    preserved = {}
    cursor.execute(
        "SELECT file_path, narrator FROM audiobooks"
        " WHERE narrator IS NOT NULL"
        " AND narrator != 'Unknown Narrator' AND narrator != ''"
    )
    for row in cursor.fetchall():
        preserved[row[0]] = row[1]
    print(f"  Preserved {len(preserved)} narrator records")
    return preserved


def _preserve_genres(cursor):
    """Preserve genre data keyed by file_path."""
    preserved = {}
    cursor.execute("""
        SELECT a.file_path, GROUP_CONCAT(g.name, '|||')
        FROM audiobooks a
        JOIN audiobook_genres ag ON a.id = ag.audiobook_id
        JOIN genres g ON ag.genre_id = g.id
        GROUP BY a.file_path
    """)
    for row in cursor.fetchall():
        if row[1]:
            preserved[row[0]] = row[1].split("|||")
    print(f"  Preserved genre data for {len(preserved)} audiobooks")
    return preserved


def _preserve_enrichment(cursor):
    """Preserve Audible enrichment data keyed by file_path."""
    preserved = {}
    field_list = ", ".join(_ENRICHMENT_FIELDS)
    cursor.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query,python.lang.security.audit.formatted-sql-query.formatted-sql-query
        f"""
        SELECT file_path, {field_list}
        FROM audiobooks
        WHERE audible_enriched_at IS NOT NULL OR isbn_enriched_at IS NOT NULL
    """  # nosec B608  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query
    )
    for row in cursor.fetchall():
        preserved[row[0]] = dict(zip(_ENRICHMENT_FIELDS, row[1:]))
    print(f"  Preserved enrichment data for {len(preserved)} audiobooks")
    return preserved


def _preserve_categories(cursor):
    """Preserve Audible categories keyed by file_path."""
    preserved: dict[str, list[dict]] = {}
    cursor.execute("""
        SELECT a.file_path, ac.category_path, ac.category_name,
               ac.root_category, ac.depth, ac.audible_category_id
        FROM audiobooks a
        JOIN audible_categories ac ON a.id = ac.audiobook_id
    """)
    for row in cursor.fetchall():
        fp = row[0]
        if fp not in preserved:
            preserved[fp] = []
        preserved[fp].append(
            {
                "category_path": row[1],
                "category_name": row[2],
                "root_category": row[3],
                "depth": row[4],
                "audible_category_id": row[5],
            }
        )
    print(f"  Preserved categories for {len(preserved)} audiobooks")
    return preserved


def _preserve_reviews(cursor):
    """Preserve editorial reviews keyed by file_path."""
    preserved: dict[str, list[dict]] = {}
    cursor.execute("""
        SELECT a.file_path, er.review_text, er.source
        FROM audiobooks a
        JOIN editorial_reviews er ON a.id = er.audiobook_id
    """)
    for row in cursor.fetchall():
        fp = row[0]
        if fp not in preserved:
            preserved[fp] = []
        preserved[fp].append({"review_text": row[1], "source": row[2]})
    print(f"  Preserved editorial reviews for {len(preserved)} audiobooks")
    return preserved


def _restore_enrichment(cursor, audiobook_id, enrichment):
    """Restore enrichment data for a single audiobook."""
    enrich_updates = []
    enrich_params = []
    for col, val in enrichment.items():
        if val is not None and col in _ENRICHMENT_COLUMNS:
            enrich_updates.append(f"{col} = ?")
            enrich_params.append(val)
    if enrich_updates:
        enrich_params.append(audiobook_id)
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"UPDATE audiobooks SET {', '.join(enrich_updates)} WHERE id = ?",  # nosec B608 — column names whitelisted
            enrich_params,
        )


def _restore_categories(cursor, audiobook_id, cats):
    """Restore Audible categories for a single audiobook."""
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


def _restore_reviews(cursor, audiobook_id, revs):
    """Restore editorial reviews for a single audiobook."""
    for rev in revs:
        cursor.execute(
            "INSERT INTO editorial_reviews (audiobook_id, review_text, source) VALUES (?, ?, ?)",
            (audiobook_id, rev["review_text"], rev["source"]),
        )


def _insert_taxonomy_items(cursor, audiobook_id, items, entity_map, table, junction_table):
    """Insert genre/era/topic items and junction rows.

    Args:
        cursor: DB cursor
        audiobook_id: Audiobook ID
        items: List of item names
        entity_map: Dict mapping name -> id (mutated in place)
        table: Entity table name ("genres", "eras", "topics")
        junction_table: Junction table name
    """
    # Build column names from table name (e.g. "genres" -> "genre_id")
    id_col = table.rstrip("s") + "_id"
    for item_name in items:
        if item_name not in entity_map:
            cursor.execute(
                f"INSERT INTO {table} (name) VALUES (?)", (item_name,)
            )  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            entity_map[item_name] = cursor.lastrowid
        cursor.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            f"INSERT INTO {junction_table} (audiobook_id, {id_col}) VALUES (?, ?)",  # nosec B608
            (audiobook_id, entity_map[item_name]),
        )


_CLEAR_TABLES = [
    "audiobook_topics",
    "audiobook_eras",
    "audiobook_genres",
    "audible_categories",
    "editorial_reviews",
    "book_authors",
    "book_narrators",
    "audiobooks",
    "topics",
    "eras",
    "genres",
    "authors",
    "narrators",
]


def import_audiobooks(conn):
    """Import audiobooks from JSON, preserving manually-populated metadata"""
    print(f"\nLoading audiobooks from: {JSON_PATH}")

    with open(JSON_PATH) as f:
        data = json.load(f)

    audiobooks = data["audiobooks"]
    print(f"Found {len(audiobooks)} audiobooks")

    cursor = conn.cursor()

    # PRESERVE existing metadata that was populated from external sources
    print("\nPreserving existing metadata...")
    preserved_content_types = _preserve_content_types(cursor)
    preserved_narrators = _preserve_narrators(cursor)
    preserved_genres = _preserve_genres(cursor)
    preserved_enrichment = _preserve_enrichment(cursor)
    preserved_categories = _preserve_categories(cursor)
    preserved_reviews = _preserve_reviews(cursor)

    # Clear existing data
    for table in _CLEAR_TABLES:
        cursor.execute(
            f"DELETE FROM {table}"
        )  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query,python.lang.security.audit.formatted-sql-query.formatted-sql-query

    print("\nImporting audiobooks...")

    # Track unique values
    genres_map: dict[str, int] = {}
    eras_map: dict[str, int] = {}
    topics_map: dict[str, int] = {}

    for idx, book in enumerate(audiobooks, 1):
        if idx % 100 == 0:
            print(f"  Processed {idx}/{len(audiobooks)} audiobooks...")

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

        # Restore enrichment data if available
        enrichment = preserved_enrichment.get(file_path)
        if enrichment:
            _restore_enrichment(cursor, audiobook_id, enrichment)

        # Restore content_type for non-enriched entries
        if not enrichment and file_path in preserved_content_types:
            cursor.execute(
                "UPDATE audiobooks SET content_type = ? WHERE id = ?",
                (preserved_content_types[file_path], audiobook_id),
            )

        # Restore Audible categories and editorial reviews
        _restore_categories(cursor, audiobook_id, preserved_categories.get(file_path, []))
        _restore_reviews(cursor, audiobook_id, preserved_reviews.get(file_path, []))

        # Handle genres, eras, topics
        genre_list = preserved_genres.get(file_path, book.get("genres", []))
        _insert_taxonomy_items(
            cursor, audiobook_id, genre_list, genres_map, "genres", "audiobook_genres"
        )
        _insert_taxonomy_items(
            cursor, audiobook_id, book.get("eras", []), eras_map, "eras", "audiobook_eras"
        )
        _insert_taxonomy_items(
            cursor, audiobook_id, book.get("topics", []), topics_map, "topics", "audiobook_topics"
        )

    conn.commit()

    _print_import_stats(
        audiobooks,
        preserved_narrators,
        preserved_genres,
        preserved_enrichment,
        preserved_categories,
        preserved_reviews,
        genres_map,
        eras_map,
        topics_map,
    )

    # Populate name-split columns and rebuild junction tables
    print("\nPopulating name columns and junction tables...")
    _populate_names_and_junctions(cursor)
    conn.commit()

    _print_database_stats(cursor, genres_map)

    # Clean up orphaned cover files
    _cleanup_orphaned_covers(cursor)

    # Optimize database
    print("\nOptimizing database...")
    cursor.execute("VACUUM")
    cursor.execute("ANALYZE")
    print("✓ Database optimized")


def _print_import_stats(
    audiobooks,
    preserved_narrators,
    preserved_genres,
    preserved_enrichment,
    preserved_categories,
    preserved_reviews,
    genres_map,
    eras_map,
    topics_map,
):
    """Print import summary statistics."""
    print(f"\n✓ Imported {len(audiobooks)} audiobooks")
    print(f"✓ Restored {len(preserved_narrators)} narrator records")
    print(f"✓ Restored genres for {len(preserved_genres)} audiobooks")
    print(f"✓ Restored enrichment for {len(preserved_enrichment)} audiobooks")
    print(f"✓ Restored categories for {len(preserved_categories)} audiobooks")
    print(f"✓ Restored reviews for {len(preserved_reviews)} audiobooks")
    print(f"✓ Total {len(genres_map)} unique genres")
    print(f"✓ Imported {len(eras_map)} eras")
    print(f"✓ Imported {len(topics_map)} topics")


def _print_database_stats(cursor, genres_map):
    """Print database statistics after import."""
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(duration_hours) FROM audiobooks")
    total_hours = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(DISTINCT author) FROM audiobooks WHERE author IS NOT NULL")
    unique_authors = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT narrator) FROM audiobooks WHERE narrator IS NOT NULL")
    unique_narrators = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE sha256_hash IS NOT NULL")
    hashed_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE asin IS NOT NULL AND asin <> ''")
    asin_count = cursor.fetchone()[0]

    print("\n=== Database Statistics ===")
    print(f"Total audiobooks: {total:,}")
    print(f"Total hours: {int(total_hours):,} ({int(total_hours / 24):,} days)")
    print(f"Unique authors: {unique_authors}")
    print(f"Unique narrators: {unique_narrators}")
    print(f"Unique genres: {len(genres_map)}")
    print(f"With SHA-256 hashes: {hashed_count:,}")
    print(f"With ASINs: {asin_count:,}")


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
