#!/usr/bin/env python3
"""
Cleanup duplicate audiobook entries from /Library/Audiobook/ folder.

These are entries where the same audiobook exists in both:
- /Library/Audiobook/Author/Book/
- /Library/Author/Book/

The /Library/Audiobook/ entries have author="Audiobook" which is incorrect.
This script removes those duplicate entries from the database and optionally
deletes the physical files to reclaim disk space.

SAFETY:
- Only removes entries that have a matching entry with REAL author
- Never removes the last copy of any audiobook
- Dry run by default
"""

import sqlite3
import sys
import os
from pathlib import Path
from argparse import ArgumentParser

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH


def format_size(size_bytes: float) -> str:
    """Format bytes into human-readable size"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


def find_audiobook_folder_duplicates(conn):
    """
    Find entries in /Library/Audiobook/ that have matching entries in /Library/Author/
    """
    cursor = conn.cursor()

    # Find all entries from /Library/Audiobook/ folder
    cursor.execute("""
        SELECT id, title, author, file_path, file_size_mb, duration_hours,
               LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) as norm_title,
               ROUND(duration_hours, 1) as duration_group
        FROM audiobooks
        WHERE file_path LIKE '%/Library/Audiobook/%'
        ORDER BY title
    """)
    audiobook_folder_entries = cursor.fetchall()

    duplicates_to_remove = []
    protected_entries = []

    for entry in audiobook_folder_entries:
        entry_id = entry[0]
        title = entry[1]
        author = entry[2]
        file_path = entry[3]
        file_size_mb = entry[4]
        duration_hours = entry[5]
        norm_title = entry[6]
        duration_group = entry[7]

        # Check if there's a matching entry with real author (not in /Audiobook/ folder)
        cursor.execute("""
            SELECT id, title, author, file_path
            FROM audiobooks
            WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) = ?
              AND ROUND(duration_hours, 1) = ?
              AND file_path NOT LIKE '%/Library/Audiobook/%'
              AND LOWER(TRIM(author)) != 'audiobook'
        """, (norm_title, duration_group))

        matching_real_entry = cursor.fetchone()

        if matching_real_entry:
            duplicates_to_remove.append({
                'id': entry_id,
                'title': title,
                'author': author,
                'file_path': file_path,
                'file_size_mb': file_size_mb,
                'real_author': matching_real_entry[2],
                'real_path': matching_real_entry[3]
            })
        else:
            # No matching entry - this is the only copy, keep it
            protected_entries.append({
                'id': entry_id,
                'title': title,
                'author': author,
                'file_path': file_path
            })

    return duplicates_to_remove, protected_entries


def cleanup_duplicates(dry_run=True, delete_files=False):
    """
    Remove duplicate entries from /Library/Audiobook/ folder
    """
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    print("=" * 70)
    print("AUDIOBOOK FOLDER DUPLICATE CLEANUP")
    print("=" * 70)
    print(f"Database: {DB_PATH}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print(f"Delete files: {'YES' if delete_files else 'NO (database only)'}")
    print()

    duplicates, protected = find_audiobook_folder_duplicates(conn)

    total_space = sum(d['file_size_mb'] for d in duplicates)

    print(f"Found {len(duplicates)} duplicate entries in /Library/Audiobook/")
    print(f"Protected entries (no matching real author): {len(protected)}")
    print(f"Potential space savings: {format_size(total_space * 1024 * 1024)}")
    print()

    if not duplicates:
        print("No duplicates to clean up!")
        conn.close()
        return

    # Show sample of duplicates
    print("=" * 70)
    print("SAMPLE DUPLICATES (first 10):")
    print("=" * 70)
    for i, dup in enumerate(duplicates[:10], 1):
        print(f"\n{i}. {dup['title'][:50]}")
        print(f"   Remove: {dup['file_path']}")
        print(f"   Keep:   {dup['real_path']}")
        print(f"   Size:   {format_size(dup['file_size_mb'] * 1024 * 1024)}")

    if len(duplicates) > 10:
        print(f"\n... and {len(duplicates) - 10} more")

    if dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to actually remove duplicates")
        if delete_files:
            print("WARNING: --delete-files will permanently delete the physical files!")
        conn.close()
        return

    # Execute removal
    print("\n" + "=" * 70)
    print("EXECUTING CLEANUP...")
    print("=" * 70)

    cursor = conn.cursor()
    removed_count = 0
    deleted_files = 0
    errors = []
    space_freed = 0

    for dup in duplicates:
        try:
            # Delete from related tables first
            cursor.execute("DELETE FROM audiobook_topics WHERE audiobook_id = ?", (dup['id'],))
            cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (dup['id'],))
            cursor.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (dup['id'],))
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (dup['id'],))

            removed_count += 1

            # Optionally delete the physical file
            if delete_files:
                file_path = Path(dup['file_path'])
                if file_path.exists():
                    file_path.unlink()
                    deleted_files += 1
                    space_freed += dup['file_size_mb']

                    # Try to remove empty parent directories
                    try:
                        file_path.parent.rmdir()  # Only removes if empty
                        file_path.parent.parent.rmdir()  # Author folder
                    except OSError:
                        pass  # Directory not empty, that's fine

            if removed_count % 100 == 0:
                print(f"  Processed {removed_count}/{len(duplicates)}...")

        except Exception as e:
            errors.append({'id': dup['id'], 'title': dup['title'], 'error': str(e)})

    conn.commit()
    conn.close()

    print("\n" + "=" * 70)
    print("CLEANUP COMPLETE")
    print("=" * 70)
    print(f"Database entries removed: {removed_count}")
    if delete_files:
        print(f"Physical files deleted: {deleted_files}")
        print(f"Disk space freed: {format_size(space_freed * 1024 * 1024)}")
    print(f"Errors: {len(errors)}")

    if errors:
        print("\nErrors encountered:")
        for err in errors[:5]:
            print(f"  - {err['title'][:40]}: {err['error']}")
        if len(errors) > 5:
            print(f"  ... and {len(errors) - 5} more errors")


def main():
    parser = ArgumentParser(description="Clean up duplicate audiobook entries from /Library/Audiobook/ folder")
    parser.add_argument('--execute', action='store_true',
                        help='Actually remove duplicates (default is dry run)')
    parser.add_argument('--delete-files', action='store_true',
                        help='Also delete the physical files (DESTRUCTIVE)')

    args = parser.parse_args()

    if args.delete_files and not args.execute:
        print("Error: --delete-files requires --execute")
        sys.exit(1)

    cleanup_duplicates(dry_run=not args.execute, delete_files=args.delete_files)


if __name__ == "__main__":
    main()
