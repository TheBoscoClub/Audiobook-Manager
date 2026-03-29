#!/usr/bin/env python3
"""
Incremental Audiobook Adder
============================
Scans library for audiobooks NOT already in the database and adds them directly.
This is much faster than a full rescan for large libraries.

Unlike scan_audiobooks.py which:
1. Scans ALL files
2. Writes to JSON
3. Requires separate import step

This script:
1. Queries DB for existing file paths
2. Scans library for new files only
3. Inserts directly into SQLite
"""

import sqlite3
import sys
from pathlib import Path
from typing import Callable, Optional

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOK_DIR, COVER_DIR, DATABASE_PATH

# Import shared utilities from scanner package
from scanner.metadata_utils import (
    extract_cover_art,
    get_file_metadata,
)
from scanner.utils.constants import SUPPORTED_FORMATS, is_cover_art_file
from scanner.utils.db_helpers import (
    ALLOWED_LOOKUP_TABLES,  # noqa: F401 — re-exported for backward compatibility
    get_or_create_lookup_id,  # noqa: F401 — re-exported for backward compatibility
    insert_audiobook,
)

# Auto-enrichment and verification (imported lazily)
_enrich_module = None
_verify_module = None


def _get_enrich_module():
    global _enrich_module
    if _enrich_module is None:
        try:
            from scripts.enrich_single import enrich_book

            _enrich_module = enrich_book
        except ImportError:
            _enrich_module = False
    return _enrich_module if _enrich_module else None


def _get_verify_module():
    global _verify_module
    if _verify_module is None:
        try:
            from scripts.verify_metadata import verify_single_book

            _verify_module = verify_single_book
        except ImportError:
            _verify_module = False
    return _verify_module if _verify_module else None


# Progress callback type
ProgressCallback = Optional[Callable[[int, int, str], None]]


def get_existing_paths(db_path: Path) -> set[str]:
    """Get all file paths already in the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM audiobooks")
    paths = {row[0] for row in cursor.fetchall()}
    conn.close()
    return paths


def find_new_audiobooks(library_dir: Path, existing_paths: set[str]) -> list[Path]:
    """Find audiobook files not already in the database."""
    all_files = []

    for ext in SUPPORTED_FORMATS:
        files = list(library_dir.rglob(f"*{ext}"))
        all_files.extend(files)

    # Filter out cover art files
    all_files = [f for f in all_files if not is_cover_art_file(f)]

    # Deduplicate: prefer main Library over /Library/Audiobook/
    main_files = [f for f in all_files if "/Library/Audiobook/" not in str(f)]
    audiobook_files = [f for f in all_files if "/Library/Audiobook/" in str(f)]
    main_stems = {f.stem for f in main_files}
    unique_audiobook = [f for f in audiobook_files if f.stem not in main_stems]
    all_files = main_files + unique_audiobook

    # Filter to only NEW files (not in database)
    new_files = [f for f in all_files if str(f) not in existing_paths]

    return new_files


def add_new_audiobooks(
    library_dir: Path = AUDIOBOOK_DIR,
    db_path: Path = DATABASE_PATH,
    cover_dir: Path = COVER_DIR,
    calculate_hashes: bool = True,
    progress_callback: ProgressCallback = None,
) -> dict:
    """
    Find and add new audiobooks to the database.

    Args:
        library_dir: Path to audiobook library
        db_path: Path to SQLite database
        cover_dir: Path to cover art directory
        calculate_hashes: Whether to calculate SHA-256 hashes
        progress_callback: Optional callback(current, total, message)

    Returns:
        dict with results: {added: int, skipped: int, errors: int, new_files: list}
    """
    added_count = 0
    skipped_count = 0
    errors_count = 0
    new_files_list: list[dict] = []

    # Get existing paths
    if progress_callback:
        progress_callback(0, 100, "Querying database for existing files...")

    existing_paths = get_existing_paths(db_path)
    print(f"Found {len(existing_paths)} existing audiobooks in database")

    # Find new files
    if progress_callback:
        progress_callback(5, 100, "Scanning library for new files...")

    new_files = find_new_audiobooks(library_dir, existing_paths)
    print(f"Found {len(new_files)} new audiobooks to add")

    if not new_files:
        if progress_callback:
            progress_callback(100, 100, "No new audiobooks found")
        return {
            "added": added_count,
            "skipped": skipped_count,
            "errors": errors_count,
            "new_files": new_files_list,
        }

    # Ensure cover directory exists
    cover_dir.mkdir(parents=True, exist_ok=True)

    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        total = len(new_files)
        for idx, filepath in enumerate(new_files, 1):
            # Calculate progress (5-95% range for processing)
            pct = 5 + int((idx / total) * 90)

            if progress_callback:
                progress_callback(
                    pct, 100, f"Processing {idx}/{total}: {filepath.name}"
                )

            print(f"[{idx:3d}/{total}] Adding: {filepath.name}")

            # Extract metadata using shared utility
            metadata = get_file_metadata(
                filepath, audiobook_dir=library_dir, calculate_hash=calculate_hashes
            )
            if not metadata:
                errors_count += 1
                continue

            # Extract cover art (tiers: embedded → sidecar → external API)
            cover_path = extract_cover_art(filepath, cover_dir, metadata=metadata)

            try:
                # Insert into database
                audiobook_id = insert_audiobook(conn, metadata, cover_path)
                conn.commit()

                # Auto-enrich from Audible + ISBN sources
                enrich_fn = _get_enrich_module()
                if enrich_fn and audiobook_id:
                    try:
                        enrich_fn(
                            book_id=audiobook_id,
                            db_path=db_path,
                            quiet=True,
                        )
                    except Exception as e:
                        print(f"  ⚠ Enrichment error (non-fatal): {e}", file=sys.stderr)

                # Verify metadata consistency
                verify_fn = _get_verify_module()
                if verify_fn and audiobook_id:
                    try:
                        verify_fn(
                            book_id=audiobook_id,
                            db_path=db_path,
                            auto_fix=True,
                            quiet=True,
                        )
                    except Exception as e:
                        print(
                            f"  ⚠ Verification error (non-fatal): {e}", file=sys.stderr
                        )

                added_count += 1
                new_files_list.append(
                    {
                        "id": audiobook_id,
                        "title": metadata.get("title"),
                        "author": metadata.get("author"),
                        "file_path": str(filepath),
                    }
                )

            except sqlite3.IntegrityError:
                # File path already exists (race condition or duplicate)
                print(f"  Skipped (already exists): {filepath.name}")
                skipped_count += 1
                conn.rollback()
            except Exception as e:
                print(f"  Error inserting: {e}")
                errors_count += 1
                conn.rollback()

        if progress_callback:
            progress_callback(100, 100, f"Complete: Added {added_count} audiobooks")

    finally:
        conn.close()

    return {
        "added": added_count,
        "skipped": skipped_count,
        "errors": errors_count,
        "new_files": new_files_list,
    }


def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Add new audiobooks to database (incremental scan)"
    )
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="Skip SHA-256 hash calculation (faster but no integrity verification)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without actually adding",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("INCREMENTAL AUDIOBOOK SCANNER")
    print("=" * 60)
    print(f"Library:  {AUDIOBOOK_DIR}")
    print(f"Database: {DATABASE_PATH}")
    print(f"Covers:   {COVER_DIR}")
    print()

    if args.dry_run:
        # Just show what would be added
        existing = get_existing_paths(DATABASE_PATH)
        new_files = find_new_audiobooks(AUDIOBOOK_DIR, existing)

        print(f"Would add {len(new_files)} new audiobooks:")
        for f in new_files[:20]:
            print(f"  - {f.name}")
        if len(new_files) > 20:
            print(f"  ... and {len(new_files) - 20} more")
        return

    # Run the incremental add
    results = add_new_audiobooks(calculate_hashes=not args.no_hash)

    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Added:   {results['added']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Errors:  {results['errors']}")

    if results["new_files"]:
        print()
        print("New audiobooks added:")
        for book in results["new_files"][:10]:
            print(f"  - {book['title']} by {book['author']}")
        if len(results["new_files"]) > 10:
            print(f"  ... and {len(results['new_files']) - 10} more")


if __name__ == "__main__":
    main()
