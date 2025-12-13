#!/usr/bin/env python3
"""
Fix author metadata for audiobooks with author="Audiobook"

These entries have author extracted incorrectly from the /Library/Audiobook/Author/
folder structure. The real author is in the next subfolder level.

This script extracts the real author from the file path.
"""

import sqlite3
import sys
from pathlib import Path
from argparse import ArgumentParser

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH


def fix_audiobook_authors(dry_run=True):
    """
    Fix author metadata for entries with author="Audiobook"
    """
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

    # Find all entries with author="Audiobook"
    cursor.execute("""
        SELECT id, title, author, file_path
        FROM audiobooks
        WHERE LOWER(TRIM(author)) = 'audiobook'
        ORDER BY title
    """)
    entries = cursor.fetchall()

    print(f"Found {len(entries)} entries with author='Audiobook'")
    print()

    updates = []
    cannot_fix = []

    for entry_id, title, author, file_path in entries:
        # Extract author from path
        # Path: /raid0/Audiobooks/Library/Audiobook/Real Author/Book Title/file.opus
        path = Path(file_path)
        parts = path.parts

        real_author = None
        if 'Library' in parts:
            library_idx = parts.index('Library')
            # Check for /Library/Audiobook/Author/ pattern
            if len(parts) > library_idx + 2 and parts[library_idx + 1].lower() == 'audiobook':
                real_author = parts[library_idx + 2]
            elif len(parts) > library_idx + 1:
                # Fallback: /Library/Author/ pattern
                potential = parts[library_idx + 1]
                if potential.lower() != 'audiobook':
                    real_author = potential

        if real_author and real_author.lower() != 'audiobook':
            updates.append({
                'id': entry_id,
                'title': title,
                'old_author': author,
                'new_author': real_author,
                'file_path': file_path
            })
        else:
            cannot_fix.append({
                'id': entry_id,
                'title': title,
                'file_path': file_path
            })

    print(f"Can fix: {len(updates)}")
    print(f"Cannot fix (need manual review): {len(cannot_fix)}")
    print()

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

    if dry_run:
        print()
        print("=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to apply changes")
        conn.close()
        return

    # Execute updates
    print()
    print("=" * 70)
    print("EXECUTING UPDATES...")
    print("=" * 70)

    updated = 0
    for upd in updates:
        cursor.execute(
            "UPDATE audiobooks SET author = ? WHERE id = ?",
            (upd['new_author'], upd['id'])
        )
        updated += 1

    conn.commit()

    # Verify
    cursor.execute("""
        SELECT COUNT(*) FROM audiobooks
        WHERE LOWER(TRIM(author)) = 'audiobook'
    """)
    remaining = cursor.fetchone()[0]

    print(f"Updated: {updated}")
    print(f"Remaining with 'Audiobook' author: {remaining}")

    conn.close()


def main():
    parser = ArgumentParser(description="Fix author metadata for audiobooks with author='Audiobook'")
    parser.add_argument('--execute', action='store_true',
                        help='Actually apply changes (default is dry run)')

    args = parser.parse_args()
    fix_audiobook_authors(dry_run=not args.execute)


if __name__ == "__main__":
    main()
