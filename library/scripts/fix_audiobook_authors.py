#!/usr/bin/env python3
"""
Fix author metadata for audiobooks with author="Audiobook"

These entries have author extracted incorrectly from the /Library/Audiobook/Author/
folder structure. The real author is in the next subfolder level.

This script extracts the real author from the file path.
"""

import sqlite3
import sys
from argparse import ArgumentParser
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH


def _extract_author_from_path(file_path):
    """Extract the real author from a file path.

    Looks for /Library/Audiobook/Author/ or /Library/Author/ patterns.
    Returns the author string or None.
    """
    parts = Path(file_path).parts

    if "Library" not in parts:
        return None

    library_idx = parts.index("Library")

    # /Library/Audiobook/Author/ pattern
    if (
        len(parts) > library_idx + 2
        and parts[library_idx + 1].lower() == "audiobook"
    ):
        return parts[library_idx + 2]

    # /Library/Author/ fallback pattern
    if len(parts) > library_idx + 1:
        potential = parts[library_idx + 1]
        if potential.lower() != "audiobook":
            return potential

    return None


def _classify_entries(entries):
    """Classify entries into fixable updates and unfixable.

    Returns (updates, cannot_fix).
    """
    updates = []
    cannot_fix = []

    for entry_id, title, author, file_path in entries:
        real_author = _extract_author_from_path(file_path)

        if real_author and real_author.lower() != "audiobook":
            updates.append({
                "id": entry_id,
                "title": title,
                "old_author": author,
                "new_author": real_author,
                "file_path": file_path,
            })
        else:
            cannot_fix.append({"id": entry_id, "title": title, "file_path": file_path})

    return updates, cannot_fix


def _print_updates_preview(updates, cannot_fix):
    """Print preview of planned updates and unfixable entries."""
    if updates:
        print("=" * 70)
        print("UPDATES (first 20):")
        print("=" * 70)
        for i, upd in enumerate(updates[:20], 1):
            print(f"{i}. {upd['title'][:50]}")
            print(f"   Old: {upd['old_author']}")
            print(f"   New: {upd['new_author']}")
        if len(updates) > 20:
            print(f"... and {len(updates) - 20} more")

    if cannot_fix:
        print()
        print("=" * 70)
        print("CANNOT FIX (need manual review):")
        print("=" * 70)
        for item in cannot_fix[:10]:
            print(f"  - {item['title'][:50]}")
            print(f"    Path: {item['file_path']}")
        if len(cannot_fix) > 10:
            print(f"... and {len(cannot_fix) - 10} more")


def _apply_author_updates(cursor, conn, updates):
    """Execute author updates and verify."""
    print()
    print("=" * 70)
    print("EXECUTING UPDATES...")
    print("=" * 70)

    for upd in updates:
        cursor.execute(
            "UPDATE audiobooks SET author = ? WHERE id = ?",
            (upd["new_author"], upd["id"]),
        )

    conn.commit()

    cursor.execute("""
        SELECT COUNT(*) FROM audiobooks
        WHERE LOWER(TRIM(author)) = 'audiobook'
    """)
    remaining = cursor.fetchone()[0]

    print(f"Updated: {len(updates)}")
    print(f"Remaining with 'Audiobook' author: {remaining}")


def fix_audiobook_authors(dry_run=True):
    """Fix author metadata for entries with author='Audiobook'."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("=" * 70)
    print("FIX AUDIOBOOK AUTHORS")
    print("=" * 70)
    print(f"Database: {DB_PATH}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print()

    cursor.execute("""
        SELECT id, title, author, file_path
        FROM audiobooks
        WHERE LOWER(TRIM(author)) = 'audiobook'
        ORDER BY title
    """)
    entries = cursor.fetchall()

    print(f"Found {len(entries)} entries with author='Audiobook'")
    print()

    updates, cannot_fix = _classify_entries(entries)

    print(f"Can fix: {len(updates)}")
    print(f"Cannot fix (need manual review): {len(cannot_fix)}")
    print()

    _print_updates_preview(updates, cannot_fix)

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")
        conn.close()
        return

    _apply_author_updates(cursor, conn, updates)
    conn.close()


def main():
    parser = ArgumentParser(
        description="Fix author metadata for audiobooks with author='Audiobook'"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes (default is dry run)",
    )

    args = parser.parse_args()
    fix_audiobook_authors(dry_run=not args.execute)


if __name__ == "__main__":
    main()
