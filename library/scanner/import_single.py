#!/usr/bin/env python3
"""
Single Directory Audiobook Importer
====================================
Imports audiobooks from a specific directory path directly to database.
Designed to be called inline by the mover script after each successful move.

Usage:
    python3 import_single.py /path/to/Library/Author/Book
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import COVER_DIR, DATABASE_PATH

# Import shared utilities
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

# Auto-enrichment and verification (imported lazily to avoid circular deps)
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


def import_directory(
    dir_path: Path, db_path: Path = DATABASE_PATH, cover_dir: Path = COVER_DIR
) -> dict:
    """
    Import all audiobooks from a specific directory.

    Args:
        dir_path: Directory containing audiobook files
        db_path: Path to SQLite database
        cover_dir: Path to cover art directory

    Returns:
        dict with {added: int, skipped: int, errors: int}
    """
    added = 0
    skipped = 0
    errors = 0

    if not dir_path.is_dir():
        return {
            "added": 0,
            "skipped": 0,
            "errors": 1,
            "error": f"Not a directory: {dir_path}",
        }

    # Find audio files in this directory (recursive for nested structure)
    audio_files: list[Path] = []
    for ext in SUPPORTED_FORMATS:
        audio_files.extend(dir_path.rglob(f"*{ext}"))

    # Filter out cover art files
    audio_files = [f for f in audio_files if not is_cover_art_file(f)]

    if not audio_files:
        return {
            "added": 0,
            "skipped": 0,
            "errors": 0,
            "message": "No audio files found",
        }

    # Check which files are already in DB
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    existing = set()
    for f in audio_files:
        cursor.execute("SELECT 1 FROM audiobooks WHERE file_path = ?", (str(f),))
        if cursor.fetchone():
            existing.add(str(f))

    new_files = [f for f in audio_files if str(f) not in existing]
    skipped = len(existing)

    if not new_files:
        conn.close()
        return {"added": 0, "skipped": skipped, "errors": 0}

    # Ensure cover directory exists
    cover_dir.mkdir(parents=True, exist_ok=True)

    try:
        for filepath in new_files:
            # Extract metadata (skip hash for speed - mover already validated)
            metadata = get_file_metadata(
                filepath, audiobook_dir=dir_path.parent, calculate_hash=False
            )
            if not metadata:
                errors += 1
                continue

            # Extract cover art (tiers: embedded → sidecar → external API)
            cover_path = extract_cover_art(filepath, cover_dir, metadata=metadata)

            try:
                audiobook_id = insert_audiobook(conn, metadata, cover_path)
                conn.commit()
                added += 1
                print(
                    f"✓ Imported: {metadata.get('title')} by {metadata.get('author')}"
                )

                # Auto-enrich from Audible + ISBN sources
                enrich_fn = _get_enrich_module()
                if enrich_fn and audiobook_id:
                    try:
                        enrich_fn(book_id=audiobook_id, db_path=db_path, quiet=False)
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
            except sqlite3.IntegrityError:
                skipped += 1
                conn.rollback()
            except Exception as e:
                print(f"✗ Error: {e}", file=sys.stderr)
                errors += 1
                conn.rollback()
    finally:
        conn.close()

    return {"added": added, "skipped": skipped, "errors": errors}


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory_path>", file=sys.stderr)
        sys.exit(1)

    dir_path = Path(sys.argv[1])

    if not dir_path.exists():
        print(f"Path does not exist: {dir_path}", file=sys.stderr)
        sys.exit(1)

    result = import_directory(dir_path)

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Import complete: {result['added']} added,"
        f" {result['skipped']} skipped, {result['errors']} errors"
    )
    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
