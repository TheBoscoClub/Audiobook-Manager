#!/usr/bin/env python3
"""
SHA-256 Hash Generator for Audiobook Library
Generates file hashes for integrity verification and duplicate detection.

Features:
- Incremental: Only hashes files without existing hash
- Resumable: Can be interrupted and resumed
- Progress tracking with ETA
- Duplicate detection report
- Direct database updates
- Parallel processing support
"""

import multiprocessing
import sqlite3
import sys
import time
from argparse import ArgumentParser
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import calculate_sha256
from config import DATABASE_PATH

# Configuration
DB_PATH = DATABASE_PATH


def hash_file_worker(args: tuple) -> tuple:
    """Worker function for parallel hashing. Returns (audiobook_id, hash, title, file_size_mb, error)"""
    audiobook_id, file_path, file_size_mb, title = args
    filepath = Path(file_path)

    if not filepath.exists():
        return (audiobook_id, None, title, file_size_mb, "File not found")

    hash_value = calculate_sha256(filepath)
    if hash_value:
        return (audiobook_id, hash_value, title, file_size_mb, None)
    else:
        return (audiobook_id, None, title, file_size_mb, "Hash calculation failed")


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def format_size(size_bytes: int | float) -> str:
    """Format bytes into human-readable size"""
    size_float = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_float < 1024:
            return f"{size_float:.1f}{unit}"
        size_float /= 1024
    return f"{size_float:.1f}PB"


def get_pending_files(conn: sqlite3.Connection, force: bool = False) -> list:
    """Get list of files that need hashing"""
    cursor = conn.cursor()

    if force:
        cursor.execute(
            """
            SELECT id, file_path, file_size_mb, title
            FROM audiobooks
            ORDER BY file_size_mb ASC
        """
        )
    else:
        cursor.execute(
            """
            SELECT id, file_path, file_size_mb, title
            FROM audiobooks
            WHERE sha256_hash IS NULL
            ORDER BY file_size_mb ASC
        """
        )

    return cursor.fetchall()


def update_hash(conn: sqlite3.Connection, audiobook_id: int, hash_value: str):
    """Update hash in database"""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE audiobooks
        SET sha256_hash = ?, hash_verified_at = ?
        WHERE id = ?
    """,
        (hash_value, datetime.now().isoformat(), audiobook_id),
    )
    conn.commit()


def find_duplicates(conn: sqlite3.Connection) -> list:
    """Find audiobooks with duplicate hashes"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT sha256_hash, COUNT(*) as count,
               GROUP_CONCAT(id) as ids,
               GROUP_CONCAT(title, ' | ') as titles,
               SUM(file_size_mb) as total_size_mb
        FROM audiobooks
        WHERE sha256_hash IS NOT NULL
        GROUP BY sha256_hash
        HAVING count > 1
        ORDER BY total_size_mb DESC
    """
    )
    return cursor.fetchall()


def generate_hashes(
    force: bool = False, limit: int | None = None, parallel: int | None = None
):
    """Main hash generation function"""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Run the scanner and import first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # Check if sha256_hash column exists, add if missing
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(audiobooks)")
    columns = [row[1] for row in cursor.fetchall()]

    if "sha256_hash" not in columns:
        print("Adding sha256_hash column to database...")
        cursor.execute("ALTER TABLE audiobooks ADD COLUMN sha256_hash TEXT")
        cursor.execute("ALTER TABLE audiobooks ADD COLUMN hash_verified_at TIMESTAMP")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audiobooks_sha256 ON audiobooks(sha256_hash)"
        )
        conn.commit()
        print("✓ Column added")

    # Get pending files
    pending = get_pending_files(conn, force)

    if limit:
        pending = pending[:limit]

    if not pending:
        print("All audiobooks already have hashes.")
        print("\nRun with --force to recalculate all hashes.")
        show_stats(conn)
        conn.close()
        return

    total_files = len(pending)
    total_size = sum(row[2] or 0 for row in pending)

    # Use parallel processing if requested
    if parallel:
        conn.close()
        generate_hashes_parallel(pending, total_files, total_size, parallel)
        return

    print(f"\n{'=' * 60}")
    print("SHA-256 Hash Generation")
    print(f"{'=' * 60}")
    print(f"Files to process: {total_files:,}")
    print(f"Total size: {format_size(total_size * 1024 * 1024)}")
    print(f"{'=' * 60}\n")

    processed = 0
    processed_size = 0
    errors = 0
    start_time = time.time()

    try:
        for audiobook_id, file_path, file_size_mb, title in pending:
            processed += 1
            file_size_mb = file_size_mb or 0

            # Progress info
            elapsed = time.time() - start_time
            if processed > 1:
                rate = processed_size / elapsed if elapsed > 0 else 0
                remaining_size = total_size - processed_size
                eta = remaining_size / rate if rate > 0 else 0
                eta_str = format_duration(eta)
            else:
                eta_str = "calculating..."

            # Truncate title for display
            display_title = title[:40] + "..." if len(title) > 40 else title

            print(f"[{processed}/{total_files}] {display_title}")
            print(f"  Size: {format_size(file_size_mb * 1024 * 1024)} | ETA: {eta_str}")

            filepath = Path(file_path)
            if not filepath.exists():
                print("  ⚠ File not found, skipping")
                errors += 1
                continue

            hash_value = calculate_sha256(filepath)

            if hash_value:
                update_hash(conn, audiobook_id, hash_value)
                print(f"  ✓ {hash_value[:16]}...")
            else:
                errors += 1

            processed_size += file_size_mb
            print()

    except KeyboardInterrupt:
        print("\n\nInterrupted! Progress has been saved.")
        print(f"Processed {processed}/{total_files} files.")
        print("Run again to continue where you left off.")
        conn.close()
        sys.exit(0)

    elapsed = time.time() - start_time

    print(f"\n{'=' * 60}")
    print("COMPLETE")
    print(f"{'=' * 60}")
    print(f"Files processed: {processed:,}")
    print(f"Data processed: {format_size(processed_size * 1024 * 1024)}")
    print(f"Time elapsed: {format_duration(elapsed)}")
    print(f"Errors: {errors}")
    if elapsed > 0:
        print(f"Average speed: {format_size(processed_size * 1024 * 1024 / elapsed)}/s")

    show_stats(conn)
    conn.close()


def generate_hashes_parallel(
    pending: list, total_files: int, total_size: float, workers: int
):
    """Generate hashes using parallel processing"""
    print(f"\n{'=' * 60}")
    print("SHA-256 Hash Generation (PARALLEL)")
    print(f"{'=' * 60}")
    print(f"Files to process: {total_files:,}")
    print(f"Total size: {format_size(total_size * 1024 * 1024)}")
    print(f"Workers: {workers}")
    print(f"{'=' * 60}\n")

    processed = 0
    processed_size = 0
    errors = 0
    start_time = time.time()

    # Open connection for updates (will batch them)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            # Submit all jobs
            futures = {
                executor.submit(hash_file_worker, task): task for task in pending
            }

            for future in as_completed(futures):
                processed += 1
                audiobook_id, hash_value, title, file_size_mb, error = future.result()
                file_size_mb = file_size_mb or 0

                # Progress info
                elapsed = time.time() - start_time
                if processed > 1:
                    rate = processed_size / elapsed if elapsed > 0 else 0
                    remaining_size = total_size - processed_size
                    eta = remaining_size / rate if rate > 0 else 0
                    eta_str = format_duration(eta)
                else:
                    eta_str = "calculating..."

                # Truncate title for display
                display_title = title[:40] + "..." if len(title) > 40 else title

                if error:
                    print(f"[{processed}/{total_files}] {display_title}")
                    print(f"  ⚠ {error}")
                    errors += 1
                elif hash_value:
                    cursor.execute(
                        """
                        UPDATE audiobooks
                        SET sha256_hash = ?, hash_verified_at = ?
                        WHERE id = ?
                    """,
                        (hash_value, datetime.now().isoformat(), audiobook_id),
                    )

                    print(f"[{processed}/{total_files}] {display_title}")
                    print(f"  ✓ {hash_value[:16]}... | ETA: {eta_str}")

                processed_size += file_size_mb

                # Commit every 50 updates for safety
                if processed % 50 == 0:
                    conn.commit()

    except KeyboardInterrupt:
        print("\n\nInterrupted! Saving progress...")
        conn.commit()
        conn.close()
        print(f"Processed {processed}/{total_files} files.")
        print("Run again to continue where you left off.")
        sys.exit(0)

    conn.commit()
    elapsed = time.time() - start_time

    print(f"\n{'=' * 60}")
    print("COMPLETE")
    print(f"{'=' * 60}")
    print(f"Files processed: {processed:,}")
    print(f"Data processed: {format_size(processed_size * 1024 * 1024)}")
    print(f"Time elapsed: {format_duration(elapsed)}")
    print(f"Errors: {errors}")
    print(f"Workers used: {workers}")
    if elapsed > 0:
        print(f"Average speed: {format_size(processed_size * 1024 * 1024 / elapsed)}/s")

    show_stats(conn)
    conn.close()


def show_stats(conn: sqlite3.Connection):
    """Show hash statistics and duplicates"""
    cursor = conn.cursor()

    # Overall stats
    cursor.execute("SELECT COUNT(*) FROM audiobooks")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM audiobooks WHERE sha256_hash IS NOT NULL")
    hashed = cursor.fetchone()[0]

    print(f"\n{'=' * 60}")
    print("STATISTICS")
    print(f"{'=' * 60}")
    print(f"Total audiobooks: {total:,}")
    print(f"With hashes: {hashed:,} ({hashed * 100 / total:.1f}%)")
    print(f"Without hashes: {total - hashed:,}")

    # Find duplicates
    duplicates = find_duplicates(conn)

    if duplicates:
        print(f"\n{'=' * 60}")
        print(f"DUPLICATES FOUND: {len(duplicates)} groups")
        print(f"{'=' * 60}")

        total_wasted = 0
        for hash_val, count, ids, titles, total_size in duplicates:
            wasted = total_size - (total_size / count)
            total_wasted += wasted

            print(f"\nHash: {hash_val[:16]}...")
            print(
                f"  Count: {count} files | Wasted space: {format_size(wasted * 1024 * 1024)}"
            )

            # Show each file - use parameterized query
            id_list = [int(i) for i in ids.split(",")]
            placeholders = ",".join("?" * len(id_list))
            cursor.execute(
                f"""
                SELECT id, title, file_path
                FROM audiobooks
                WHERE id IN ({placeholders})
            """,
                id_list,
            )
            for row in cursor.fetchall():
                print(f"  - [{row[0]}] {row[1][:50]}")
                print(f"    {row[2]}")

        print(f"\n{'=' * 60}")
        print(f"Total wasted space: {format_size(total_wasted * 1024 * 1024)}")
        print(f"{'=' * 60}")
    else:
        print("\n✓ No duplicates found")


def verify_hashes(sample_size: int = 10):
    """Verify a sample of existing hashes for integrity"""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, file_path, sha256_hash, title, file_size_mb
        FROM audiobooks
        WHERE sha256_hash IS NOT NULL
        ORDER BY RANDOM()
        LIMIT ?
    """,
        (sample_size,),
    )

    samples = cursor.fetchall()

    if not samples:
        print("No hashed files found to verify.")
        return

    print(f"\n{'=' * 60}")
    print(f"Verifying {len(samples)} random files...")
    print(f"{'=' * 60}\n")

    passed = 0
    failed = 0
    missing = 0

    for audiobook_id, file_path, stored_hash, title, file_size in samples:
        display_title = title[:40] + "..." if len(title) > 40 else title
        print(f"Checking: {display_title}")

        filepath = Path(file_path)
        if not filepath.exists():
            print("  ⚠ File missing")
            missing += 1
            continue

        current_hash = calculate_sha256(filepath)

        if current_hash == stored_hash:
            print("  ✓ Hash verified")
            passed += 1
        else:
            print("  ✗ HASH MISMATCH!")
            print(f"    Stored:  {stored_hash[:32]}...")
            if current_hash:
                print(f"    Current: {current_hash[:32]}...")
            else:
                print("    Current: (failed to calculate)")
            failed += 1

    print(f"\n{'=' * 60}")
    print("VERIFICATION RESULTS")
    print(f"{'=' * 60}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Missing files: {missing}")

    if failed > 0:
        print("\n⚠ Some files have changed since hashing!")
        print("This could indicate corruption or modification.")

    conn.close()


def main():
    cpu_count = multiprocessing.cpu_count()
    parser = ArgumentParser(description="Generate SHA-256 hashes for audiobook library")
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Recalculate all hashes, even existing ones",
    )
    parser.add_argument(
        "--limit", "-l", type=int, help="Limit number of files to process"
    )
    parser.add_argument(
        "--parallel",
        "-p",
        type=int,
        nargs="?",
        const=cpu_count,
        metavar="N",
        help=f"Use parallel processing with N workers (default: {cpu_count} CPUs)",
    )
    parser.add_argument(
        "--stats", "-s", action="store_true", help="Show statistics only"
    )
    parser.add_argument(
        "--duplicates", "-d", action="store_true", help="Show duplicates report only"
    )
    parser.add_argument(
        "--verify",
        "-v",
        type=int,
        nargs="?",
        const=10,
        help="Verify random sample of hashes (default: 10)",
    )

    args = parser.parse_args()

    if args.stats or args.duplicates:
        conn = sqlite3.connect(DB_PATH)
        show_stats(conn)
        conn.close()
    elif args.verify:
        verify_hashes(args.verify)
    else:
        generate_hashes(force=args.force, limit=args.limit, parallel=args.parallel)


if __name__ == "__main__":
    main()
