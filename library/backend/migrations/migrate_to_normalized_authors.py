"""
Phase 2 Data Migration: Populate normalized author/narrator tables
from existing flat text columns in the audiobooks table.

Usage:
    python -m library.backend.migrations.migrate_to_normalized_authors [--db-path PATH] [--dry-run]

Idempotent: safe to run multiple times. Uses INSERT OR IGNORE for deduplication.
"""

import argparse
import logging
import sqlite3

from library.backend.name_parser import generate_sort_name, is_group_name, parse_names

logger = logging.getLogger(__name__)


def migrate(db_path: str, dry_run: bool = False) -> dict:
    """Run the author/narrator normalization migration.

    Returns:
        Dict with migration statistics.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    stats = {
        "books_processed": 0,
        "authors_created": 0,
        "narrators_created": 0,
        "author_links": 0,
        "narrator_links": 0,
        "group_redirections": 0,
        "ambiguous": [],
    }

    rows = conn.execute("SELECT id, title, author, narrator FROM audiobooks").fetchall()

    for row in rows:
        book_id = row["id"]
        stats["books_processed"] += 1

        # --- Authors ---
        author_names = parse_names(row["author"]) if row["author"] else []
        narrator_names = parse_names(row["narrator"]) if row["narrator"] else []

        # Redirect group names from author to narrator
        redirected = []
        clean_authors = []
        for name in author_names:
            if is_group_name(name):
                redirected.append(name)
                stats["group_redirections"] += 1
            else:
                clean_authors.append(name)

        # Add redirected names to narrators (avoid duplicates)
        for name in redirected:
            if name not in narrator_names:
                narrator_names.append(name)

        # Insert authors and link
        for pos, name in enumerate(clean_authors):
            sort_name = generate_sort_name(name)
            if not sort_name:
                continue
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO authors (name, sort_name) VALUES (?, ?)",
                    (name, sort_name),
                )
                author_id = conn.execute(
                    "SELECT id FROM authors WHERE name = ?", (name,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO book_authors"
                    " (book_id, author_id, position) VALUES (?, ?, ?)",
                    (book_id, author_id, pos),
                )
                stats["author_links"] += 1

        # Insert narrators and link
        for pos, name in enumerate(narrator_names):
            sort_name = generate_sort_name(name)
            if not sort_name:
                continue
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO narrators (name, sort_name) VALUES (?, ?)",
                    (name, sort_name),
                )
                narrator_id = conn.execute(
                    "SELECT id FROM narrators WHERE name = ?", (name,)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO book_narrators"
                    " (book_id, narrator_id, position) VALUES (?, ?, ?)",
                    (book_id, narrator_id, pos),
                )
                stats["narrator_links"] += 1

    if not dry_run:
        conn.commit()

    stats["authors_created"] = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[
        0
    ]
    stats["narrators_created"] = conn.execute(
        "SELECT COUNT(*) FROM narrators"
    ).fetchone()[0]

    conn.close()

    logger.info(
        "Migration complete: %d books, %d authors, %d narrators,"
        " %d author-links, %d narrator-links, %d group redirections",
        stats["books_processed"],
        stats["authors_created"],
        stats["narrators_created"],
        stats["author_links"],
        stats["narrator_links"],
        stats["group_redirections"],
    )

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Migrate to normalized authors/narrators"
    )
    parser.add_argument("--db-path", type=str, help="Path to database")
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    args = parser.parse_args()

    db_path = args.db_path
    if not db_path:
        from library.backend.config import DATABASE_PATH

        db_path = str(DATABASE_PATH)

    result = migrate(db_path, dry_run=args.dry_run)
    print(f"Migration stats: {result}")
