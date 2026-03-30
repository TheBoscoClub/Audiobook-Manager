"""
Phase 2 Data Migration: Populate normalized author/narrator tables
from existing flat text columns in the audiobooks table.

Usage:
    python -m library.backend.migrations.migrate_to_normalized_authors \
        [--db-path PATH] [--dry-run]

Idempotent: safe to run multiple times. Uses INSERT OR IGNORE for deduplication.
Deduplicates case-insensitively and accent-insensitively (Miéville = Mieville).
"""

import argparse
import logging
import sqlite3
import unicodedata
from typing import Any

from backend.name_parser import (
    clean_name,
    generate_sort_name,
    has_role_suffix,
    is_brand_name,
    is_group_name,
    is_junk_name,
    normalize_for_dedup,
    parse_names,
)

logger = logging.getLogger(__name__)


# Preferred display forms for group names (title case)
_GROUP_DISPLAY = {
    "full cast": "Full Cast",
    "bbc radio": "BBC Radio",
    "bbc radio 4": "BBC Radio 4",
    "bbc radio drama": "BBC Radio Drama",
    "various authors": "Various Authors",
    "various narrators": "Various Narrators",
    "various": "Various",
}


def _normalize_group_case(name: str) -> str:
    """Normalize group name casing to preferred display form."""
    if not name:
        return name
    lower = name.strip().lower()
    if lower in _GROUP_DISPLAY:
        return _GROUP_DISPLAY[lower]
    return name


def _find_canonical(seen: dict, name: str) -> str | None:
    """Find the canonical name for dedup, or return None if this is new.

    Uses accent-insensitive, case-insensitive normalization.
    Prefers the version with more proper casing (title case > lowercase)
    and accented characters (Miéville > Mieville).
    """
    key = normalize_for_dedup(name)
    if key in seen:
        existing = seen[key]
        # Prefer version with accents (longer NFD = has accents)
        # Prefer version with title case over all-lower
        if _name_quality(name) > _name_quality(existing):
            seen[key] = name
            return name
        return existing
    seen[key] = name
    return None


def _name_quality(name: str) -> int:
    """Score a name variant for quality — higher is better.

    Prefers: proper casing, accented characters, longer form.
    """
    score = 0
    # Prefer title case or mixed case over all-lower
    if name != name.lower():
        score += 10
    # Prefer accented characters (Miéville > Mieville)
    if any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", name)):
        score += 5
    # Prefer longer form slightly (M. R. James > M.R. James)
    score += len(name)
    return score


def migrate(db_path: str, dry_run: bool = False) -> dict:
    """Run the author/narrator normalization migration.

    Returns:
        Dict with migration statistics.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Clear existing normalized data for clean re-migration
    if not dry_run:
        conn.execute("DELETE FROM book_authors")
        conn.execute("DELETE FROM book_narrators")
        conn.execute("DELETE FROM authors")
        conn.execute("DELETE FROM narrators")

    stats: dict[str, Any] = {
        "books_processed": 0,
        "authors_created": 0,
        "narrators_created": 0,
        "author_links": 0,
        "narrator_links": 0,
        "group_redirections": 0,
        "role_excluded": 0,
        "brand_excluded": 0,
        "junk_excluded": 0,
        "dedup_merged": 0,
        "ambiguous": [],
    }

    # Track seen names for dedup (normalized_key -> canonical_name)
    seen_authors: dict[str, str] = {}
    seen_narrators: dict[str, str] = {}

    rows = conn.execute("SELECT id, title, author, narrator FROM audiobooks").fetchall()

    for row in rows:
        book_id = row["id"]
        stats["books_processed"] += 1

        # --- Authors ---
        author_names = parse_names(row["author"]) if row["author"] else []
        narrator_names = parse_names(row["narrator"]) if row["narrator"] else []

        # Clean each name (strip credentials, trailing roles)
        author_names = [clean_name(n) for n in author_names]
        narrator_names = [clean_name(n) for n in narrator_names]

        # Normalize group name casing (prefer title case: "Full Cast" not "full cast")
        author_names = [_normalize_group_case(n) for n in author_names]
        narrator_names = [_normalize_group_case(n) for n in narrator_names]

        # Redirect group names from author to narrator, exclude role-suffixed,
        # brand/publisher names, and junk entries
        redirected = []
        clean_authors = []
        for name in author_names:
            if not name or is_junk_name(name):
                stats["junk_excluded"] += 1
            elif is_group_name(name):
                redirected.append(name)
                stats["group_redirections"] += 1
            elif has_role_suffix(name):
                stats["role_excluded"] += 1
            elif is_brand_name(name):
                stats["brand_excluded"] += 1
            else:
                clean_authors.append(name)

        # Add redirected names to narrators (avoid duplicates)
        for name in redirected:
            if name not in narrator_names:
                narrator_names.append(name)

        # Insert authors and link (with dedup)
        for pos, name in enumerate(clean_authors):
            # Check for dedup match
            canonical = _find_canonical(seen_authors, name)
            if canonical is not None:
                name = canonical
                stats["dedup_merged"] += 1

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

        # Insert narrators and link (skip brands/junk, with dedup)
        for pos, name in enumerate(narrator_names):
            if not name or is_junk_name(name):
                stats["junk_excluded"] += 1
                continue
            if is_brand_name(name):
                stats["brand_excluded"] += 1
                continue

            # Check for dedup match
            canonical = _find_canonical(seen_narrators, name)
            if canonical is not None:
                name = canonical
                stats["dedup_merged"] += 1

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
        " %d author-links, %d narrator-links, %d group redirections,"
        " %d dedup merges, %d junk excluded",
        stats["books_processed"],
        stats["authors_created"],
        stats["narrators_created"],
        stats["author_links"],
        stats["narrator_links"],
        stats["group_redirections"],
        stats["dedup_merged"],
        stats["junk_excluded"],
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
        try:
            from library.backend.config import DATABASE_PATH
        except ModuleNotFoundError:
            from backend.config import DATABASE_PATH

        db_path = str(DATABASE_PATH)

    result = migrate(db_path, dry_run=args.dry_run)
    print(f"Migration stats: {result}")
