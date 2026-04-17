#!/usr/bin/env python3
"""
Duplicate Audiobook Finder
Finds and reports duplicate audiobooks based on SHA-256 hashes.

Features:
- Identifies exact file duplicates
- Calculates wasted storage space
- Exports duplicate list to JSON
- Suggests which copies to keep (based on path/format preferences)
"""

import json
import sqlite3
import sys
from argparse import ArgumentParser
from datetime import datetime
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


def find_duplicates(conn: sqlite3.Connection) -> list:
    """Find all duplicate groups"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sha256_hash, COUNT(*) as count
        FROM audiobooks
        WHERE sha256_hash IS NOT NULL
        GROUP BY sha256_hash
        HAVING count > 1
        ORDER BY count DESC
    """)
    return cursor.fetchall()


def get_duplicate_details(conn: sqlite3.Connection, hash_value: str) -> list:
    """Get details of all files with a specific hash"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, title, author, file_path, file_size_mb, format,
               duration_hours, created_at
        FROM audiobooks
        WHERE sha256_hash = ?
        ORDER BY file_path
    """,
        (hash_value,),
    )
    return cursor.fetchall()


def suggest_keep(files: list) -> int:
    """Suggest which file to keep based on preferences"""
    format_scores = {"m4b": 100, "opus": 50, "m4a": 25}

    scored = []
    for f in files:
        file_id, _title, _author, path, _size, fmt, _duration, _created = f
        score = format_scores.get(fmt, 0) - int(len(path) / 10)
        scored.append((score, file_id))

    scored.sort(reverse=True)
    return scored[0][1]


def _print_library_stats(total_books, hashed_books, total_size):
    """Print overall library statistics."""
    print(f"\n{'=' * 70}")
    print("AUDIOBOOK DUPLICATE REPORT")
    print(f"{'=' * 70}")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total audiobooks: {total_books:,}")
    print(f"With hashes: {hashed_books:,} ({hashed_books * 100 / total_books:.1f}%)")
    print(f"Total library size: {format_size(total_size * 1024 * 1024)}")

    if hashed_books < total_books:
        print(f"\n\u26a0 {total_books - hashed_books} audiobooks not yet hashed.")
        print("  Run: python3 scripts/generate_hashes.py")


def _get_library_stats(conn):
    """Fetch basic library statistics. Returns (total, hashed, total_size)."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total_books = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE sha256_hash IS NOT NULL")
    hashed_books = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(file_size_mb) FROM audiobooks")
    total_size = cursor.fetchone()[0] or 0
    return total_books, hashed_books, total_size


def _build_duplicate_group(conn, hash_value, count, idx, total_groups):
    """Build export data and print info for one duplicate group."""
    files = get_duplicate_details(conn, hash_value)
    file_size = files[0][4] if files else 0
    wasted = file_size * (count - 1)
    keep_id = suggest_keep(files)

    print(f"\n[{idx}/{total_groups}] Duplicate Group (Hash: {hash_value[:16]}...)")
    print(
        f"    Files: {count} | Size each: {format_size(file_size * 1024 * 1024)} | "
        f"Wasted: {format_size(wasted * 1024 * 1024)}"
    )
    print(f"    Title: {files[0][1][:60]}")
    print(f"    Author: {files[0][2]}")

    group_files = []
    for f in files:
        file_id, _title, _author, path, _size, fmt, _duration, _created = f
        is_keep = file_id == keep_id
        marker = "KEEP" if is_keep else "    "
        print(f"    [{marker}] ID:{file_id} | {fmt.upper():4} | {path}")
        group_files.append(
            {
                "id": file_id,
                "path": path,
                "format": fmt,
                "suggested_action": "keep" if is_keep else "remove",
            }
        )

    return {
        "hash": hash_value,
        "count": count,
        "file_size_mb": file_size,
        "wasted_mb": wasted,
        "title": files[0][1],
        "author": files[0][2],
        "suggested_keep_id": keep_id,
        "files": group_files,
    }


def _export_report(
    export_data, total_books, duplicates, total_wasted_files, total_wasted_space, export_path
):
    """Export duplicate report to JSON file."""
    output_path = Path(export_path) if export_path else Path("duplicates.json")
    with open(output_path, "w") as f:
        json.dump(
            {
                "generated_at": datetime.now().isoformat(),
                "summary": {
                    "total_audiobooks": total_books,
                    "duplicate_groups": len(duplicates),
                    "extra_copies": total_wasted_files,
                    "wasted_space_mb": total_wasted_space,
                },
                "duplicates": export_data,
            },
            f,
            indent=2,
        )
    print(f"\n\u2713 Exported to: {output_path}")


def generate_report(export_json: bool = False, export_path: str | None = None):
    """Generate duplicate report"""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Run the scanner and import first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    total_books, hashed_books, total_size = _get_library_stats(conn)
    _print_library_stats(total_books, hashed_books, total_size)

    duplicates = find_duplicates(conn)
    if not duplicates:
        print(f"\n{'=' * 70}")
        print("\u2713 No duplicates found!")
        print(f"{'=' * 70}")
        conn.close()
        return

    total_duplicate_files = sum(count for _, count in duplicates)
    total_wasted_files = total_duplicate_files - len(duplicates)

    print(f"\n{'=' * 70}")
    print(f"DUPLICATES FOUND: {len(duplicates)} unique files with duplicates")
    print(f"Total duplicate copies: {total_wasted_files}")
    print(f"{'=' * 70}")

    export_data = []
    total_wasted_space = 0
    for idx, (hash_value, count) in enumerate(duplicates, 1):
        group = _build_duplicate_group(conn, hash_value, count, idx, len(duplicates))
        export_data.append(group)
        total_wasted_space += group["wasted_mb"]

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"Duplicate groups: {len(duplicates)}")
    print(f"Extra copies: {total_wasted_files}")
    print(f"Wasted space: {format_size(total_wasted_space * 1024 * 1024)}")
    print(f"Potential savings: {total_wasted_space * 100 / total_size:.1f}% of library")

    if export_json:
        _export_report(
            export_data,
            total_books,
            duplicates,
            total_wasted_files,
            total_wasted_space,
            export_path,
        )

    conn.close()


def _build_removal_plan(duplicates, conn):
    """Build lists of files to remove and keepers from duplicate groups.

    Returns (files_to_remove, protected_keepers, space_to_free).
    """
    files_to_remove = []
    protected_keepers = []
    space_to_free = 0

    for hash_value, count in duplicates:
        files = get_duplicate_details(conn, hash_value)
        if len(files) < 2:
            continue

        files_sorted = sorted(files, key=lambda x: x[0])
        keeper = files_sorted[0]
        protected_keepers.append(
            {"id": keeper[0], "title": keeper[1], "path": keeper[3], "hash": hash_value}
        )

        for f in files_sorted[1:]:
            file_id, title, _author, path, size, _fmt, _duration, _created = f
            files_to_remove.append(
                {"id": file_id, "path": path, "size_mb": size, "hash": hash_value, "title": title}
            )
            space_to_free += size

    return files_to_remove, protected_keepers, space_to_free


def _print_removal_plan(files_to_remove, protected_keepers, space_to_free):
    """Print the dry-run removal plan."""
    print("\n" + "=" * 70)
    print("PROTECTED FILES (will be kept):")
    print("=" * 70)
    for k in protected_keepers[:10]:
        print(f"  [KEEP] ID:{k['id']} | {k['title'][:50]}")
    if len(protected_keepers) > 10:
        print(f"  ... and {len(protected_keepers) - 10} more keepers")

    print("\n" + "=" * 70)
    print("FILES TO REMOVE:")
    print("=" * 70)
    for f in files_to_remove[:20]:
        print(f"  [DEL]  ID:{f['id']} | {f['title'][:50]}")
    if len(files_to_remove) > 20:
        print(f"  ... and {len(files_to_remove) - 20} more")

    print("\n" + "=" * 70)
    print("\u26a0 This is a DRY RUN. No files were removed.")
    print("  Run with --execute to actually remove files.")
    print("=" * 70)


def _verify_safe_deletions(cursor, files_to_remove):
    """Verify each file still has remaining copies before deletion.

    Returns (verified_safe, blocked).
    """
    verified_safe = []
    blocked = []

    for f in files_to_remove:
        cursor.execute(
            "SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ? AND id != ?",
            (f["hash"], f["id"]),
        )
        remaining = cursor.fetchone()[0]
        if remaining >= 1:
            verified_safe.append(f)
        else:
            blocked.append(f)
            print(f"  \u26a0 BLOCKED: {f['title'][:50]} - would delete last copy!")

    return verified_safe, blocked


def _delete_audiobook_entry(cursor, file_id):
    """Delete an audiobook and its related junction table rows."""
    for table in ("audiobook_topics", "audiobook_eras", "audiobook_genres"):
        cursor.execute(f"DELETE FROM {table} WHERE audiobook_id = ?", (file_id,))  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    cursor.execute("DELETE FROM audiobooks WHERE id = ?", (file_id,))


def _execute_deletions(cursor, verified_safe):
    """Delete verified-safe files and DB entries. Returns (removed, errors)."""
    removed = 0
    errors = 0

    for f in verified_safe:
        try:
            path = Path(f["path"])
            if path.exists():
                path.unlink()
                print(f"  \u2713 Deleted file: {path.name}")
            _delete_audiobook_entry(cursor, f["id"])
            removed += 1
        except Exception as e:
            errors += 1
            print(f"  \u2717 Error: {f['path']}: {e}")

    return removed, errors


def remove_duplicates(dry_run: bool = True):
    """
    Remove duplicate files (keeps one copy per unique hash).

    SAFETY GUARANTEES:
    1. Will NEVER delete the last remaining copy of any audiobook
    2. Always keeps the file with the lowest ID (first imported)
    3. Verifies each hash has remaining copies before deletion
    4. Double-checks file existence before database removal
    """
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    duplicates = find_duplicates(conn)

    if not duplicates:
        print("No duplicates found.")
        conn.close()
        return

    files_to_remove, protected_keepers, space_to_free = _build_removal_plan(duplicates, conn)

    print(f"\n{'=' * 70}")
    print("DUPLICATE REMOVAL PLAN" + (" (DRY RUN)" if dry_run else ""))
    print(f"{'=' * 70}")
    print(f"Files to remove: {len(files_to_remove)}")
    print(f"Files protected (keepers): {len(protected_keepers)}")
    print(f"Space to free: {format_size(space_to_free * 1024 * 1024)}")

    if dry_run:
        _print_removal_plan(files_to_remove, protected_keepers, space_to_free)
        conn.close()
        return

    # Execute mode: verify then delete
    print("\n" + "=" * 70)
    print("SAFETY VERIFICATION")
    print("=" * 70)

    verified_safe, blocked = _verify_safe_deletions(cursor, files_to_remove)

    if blocked:
        print(f"\n  {len(blocked)} files blocked from deletion (last copies)")
    print(f"  {len(verified_safe)} files verified safe to delete")

    if not verified_safe:
        print("\n\u2717 No files safe to delete.")
        conn.close()
        return

    print("\n" + "=" * 70)
    print("REMOVING FILES...")
    print("=" * 70)

    removed, errors = _execute_deletions(cursor, verified_safe)
    conn.commit()

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"\u2713 Removed: {removed} files")
    print(f"\u2717 Errors: {errors}")
    print(f"\u2298 Blocked: {len(blocked)} (protected last copies)")
    freed = sum(f["size_mb"] for f in verified_safe)
    print(f"\U0001f4be Space freed: {format_size(freed * 1024 * 1024)}")

    conn.close()


def main():
    parser = ArgumentParser(description="Find and report duplicate audiobooks")
    parser.add_argument("--json", "-j", action="store_true", help="Export duplicate report to JSON")
    parser.add_argument("--output", "-o", type=str, help="Output path for JSON export")
    parser.add_argument("--remove", action="store_true", help="Show removal plan (dry run)")
    parser.add_argument(
        "--execute", action="store_true", help="Actually remove duplicate files (DESTRUCTIVE)"
    )

    args = parser.parse_args()

    if args.remove or args.execute:
        remove_duplicates(dry_run=not args.execute)
    else:
        generate_report(export_json=args.json, export_path=args.output)


if __name__ == "__main__":
    main()
