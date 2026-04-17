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
from argparse import ArgumentParser
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

DB_PATH = DATABASE_PATH


def format_size(size_bytes: float) -> str:
    """Format bytes into human-readable size"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


def find_audiobook_folder_duplicates(conn):
    """
    Find entries in /Library/Audiobook/ that have matching entries in /Library/Author/
    """
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, file_path, file_size_mb, duration_hours,
               LOWER(TRIM(REPLACE(REPLACE(REPLACE(
                   title, ':', ''), '-', ''), '  ', ' '))) as norm_title,
               ROUND(duration_hours, 1) as duration_group
        FROM audiobooks
        WHERE file_path LIKE '%/Library/Audiobook/%'
        ORDER BY title
    """)
    audiobook_folder_entries = cursor.fetchall()

    duplicates_to_remove = []
    protected_entries = []

    for entry in audiobook_folder_entries:
        dup, protected = _classify_entry(cursor, entry)
        if dup:
            duplicates_to_remove.append(dup)
        elif protected:
            protected_entries.append(protected)

    return duplicates_to_remove, protected_entries


def _classify_entry(cursor, entry):
    """Classify a single /Library/Audiobook/ entry as duplicate or protected.

    Returns (dup_dict, None) or (None, protected_dict).
    """
    entry_id, title, author, file_path, file_size_mb = entry[0:5]
    norm_title, duration_group = entry[6], entry[7]

    cursor.execute(
        """
        SELECT id, title, author, file_path
        FROM audiobooks
        WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(
            title, ':', ''), '-', ''), '  ', ' '))) = ?
          AND ROUND(duration_hours, 1) = ?
          AND file_path NOT LIKE '%/Library/Audiobook/%'
          AND LOWER(TRIM(author)) != 'audiobook'
    """,
        (norm_title, duration_group),
    )
    match = cursor.fetchone()

    if match:
        return {
            "id": entry_id,
            "title": title,
            "author": author,
            "file_path": file_path,
            "file_size_mb": file_size_mb,
            "real_author": match[2],
            "real_path": match[3],
        }, None

    return None, {"id": entry_id, "title": title, "author": author, "file_path": file_path}


def _print_duplicate_samples(duplicates):
    """Print a sample of duplicate entries."""
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


def _delete_audiobook_entry(cursor, audiobook_id):
    """Delete an audiobook and its related junction table rows."""
    for table in ("audiobook_topics", "audiobook_eras", "audiobook_genres"):
        cursor.execute(f"DELETE FROM {table} WHERE audiobook_id = ?", (audiobook_id,))  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    cursor.execute("DELETE FROM audiobooks WHERE id = ?", (audiobook_id,))


def _delete_physical_file(dup):
    """Delete the physical file and try to clean up empty parent dirs.

    Returns (deleted: bool, space_mb: float).
    """
    file_path = Path(dup["file_path"])
    if not file_path.exists():
        return False, 0

    file_path.unlink()

    # Try to remove empty parent directories
    try:
        file_path.parent.rmdir()
        file_path.parent.parent.rmdir()
    except OSError:
        pass  # Directory not empty, that's fine

    return True, dup["file_size_mb"]


def _execute_cleanup(conn, duplicates, delete_files):
    """Execute the actual cleanup: delete DB entries and optionally files."""
    cursor = conn.cursor()
    removed_count = 0
    deleted_files = 0
    errors = []
    space_freed = 0

    for dup in duplicates:
        try:
            _delete_audiobook_entry(cursor, dup["id"])
            removed_count += 1

            if delete_files:
                deleted, space = _delete_physical_file(dup)
                if deleted:
                    deleted_files += 1
                    space_freed += space

            if removed_count % 100 == 0:
                print(f"  Processed {removed_count}/{len(duplicates)}...")

        except Exception as e:
            errors.append({"id": dup["id"], "title": dup["title"], "error": str(e)})

    conn.commit()
    return removed_count, deleted_files, space_freed, errors


def _print_cleanup_results(removed_count, deleted_files, space_freed, errors, delete_files):
    """Print the cleanup completion summary."""
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


def cleanup_duplicates(dry_run=True, delete_files=False):
    """Remove duplicate entries from /Library/Audiobook/ folder."""
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
    total_space = sum(d["file_size_mb"] for d in duplicates)

    print(f"Found {len(duplicates)} duplicate entries in /Library/Audiobook/")
    print(f"Protected entries (no matching real author): {len(protected)}")
    print(f"Potential space savings: {format_size(total_space * 1024 * 1024)}")
    print()

    if not duplicates:
        print("No duplicates to clean up!")
        conn.close()
        return

    _print_duplicate_samples(duplicates)

    if dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN - No changes made")
        print("=" * 70)
        print("Run with --execute to actually remove duplicates")
        if delete_files:
            print("WARNING: --delete-files will permanently delete the physical files!")
        conn.close()
        return

    print("\n" + "=" * 70)
    print("EXECUTING CLEANUP...")
    print("=" * 70)

    removed_count, deleted_files_count, space_freed, errors = _execute_cleanup(
        conn, duplicates, delete_files
    )
    conn.close()

    _print_cleanup_results(removed_count, deleted_files_count, space_freed, errors, delete_files)


def main():
    parser = ArgumentParser(
        description=("Clean up duplicate audiobook entries from /Library/Audiobook/ folder")
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually remove duplicates (default is dry run)"
    )
    parser.add_argument(
        "--delete-files", action="store_true", help="Also delete the physical files (DESTRUCTIVE)"
    )

    args = parser.parse_args()

    if args.delete_files and not args.execute:
        print("Error: --delete-files requires --execute")
        sys.exit(1)

    cleanup_duplicates(dry_run=not args.execute, delete_files=args.delete_files)


if __name__ == "__main__":
    main()
