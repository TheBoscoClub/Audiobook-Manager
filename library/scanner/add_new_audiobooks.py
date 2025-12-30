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

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOK_DIR, COVER_DIR, DATABASE_PATH
from utils import calculate_sha256

SUPPORTED_FORMATS = [".m4b", ".opus", ".m4a", ".mp3"]

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
    all_files = [f for f in all_files if ".cover." not in f.name.lower()]

    # Deduplicate: prefer main Library over /Library/Audiobook/
    main_files = [f for f in all_files if "/Library/Audiobook/" not in str(f)]
    audiobook_files = [f for f in all_files if "/Library/Audiobook/" in str(f)]
    main_stems = {f.stem for f in main_files}
    unique_audiobook = [f for f in audiobook_files if f.stem not in main_stems]
    all_files = main_files + unique_audiobook

    # Filter to only NEW files (not in database)
    new_files = [f for f in all_files if str(f) not in existing_paths]

    return new_files


def get_file_metadata(filepath: Path, calculate_hash: bool = True) -> Optional[dict]:
    """Extract metadata from audiobook file using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(filepath),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"  Warning: Could not read {filepath.name}", file=sys.stderr)
            return None

        data = json.loads(result.stdout)
        format_data = data.get("format", {})
        tags = format_data.get("tags", {})
        tags_normalized = {k.lower(): v for k, v in tags.items()}

        # Duration
        duration_sec = float(format_data.get("duration", 0))
        duration_hours = duration_sec / 3600

        # Extract author from folder structure
        author_from_path = None
        parts = filepath.parts
        if "Library" in parts:
            library_idx = parts.index("Library")
            if len(parts) > library_idx + 1:
                potential_author = parts[library_idx + 1]
                if potential_author.lower() == "audiobook":
                    if len(parts) > library_idx + 2:
                        author_from_path = parts[library_idx + 2]
                else:
                    author_from_path = potential_author

        # Author (priority order)
        author = None
        for field in ["artist", "album_artist", "author", "writer", "creator"]:
            if field in tags_normalized and tags_normalized[field]:
                author = tags_normalized[field]
                break
        if not author:
            author = author_from_path or "Unknown Author"

        # Narrator (priority order)
        narrator = None
        for field in ["narrator", "composer", "performer", "read_by", "narrated_by", "reader"]:
            if field in tags_normalized and tags_normalized[field]:
                val = tags_normalized[field]
                if val.lower() != author.lower():
                    narrator = val
                    break
        if not narrator:
            narrator = "Unknown Narrator"

        # SHA-256 hash
        file_hash = None
        hash_verified_at = None
        if calculate_hash:
            file_hash = calculate_sha256(filepath)
            if file_hash:
                hash_verified_at = datetime.now().isoformat()

        return {
            "title": tags_normalized.get("title", tags_normalized.get("album", filepath.stem)),
            "author": author,
            "narrator": narrator,
            "publisher": tags_normalized.get("publisher", tags_normalized.get("label", "Unknown Publisher")),
            "genre": tags_normalized.get("genre", "Uncategorized"),
            "year": tags_normalized.get("date", tags_normalized.get("year", "")),
            "description": tags_normalized.get("comment", tags_normalized.get("description", "")),
            "duration_hours": round(duration_hours, 2),
            "duration_formatted": f"{int(duration_hours)}h {int((duration_hours % 1) * 60)}m",
            "file_size_mb": round(filepath.stat().st_size / (1024 * 1024), 2),
            "file_path": str(filepath),
            "series": tags_normalized.get("series", ""),
            "series_part": tags_normalized.get("series-part", ""),
            "sha256_hash": file_hash,
            "hash_verified_at": hash_verified_at,
            "format": filepath.suffix.lower().replace(".", ""),
        }

    except subprocess.TimeoutExpired:
        print(f"  Warning: Timeout reading {filepath.name}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error processing {filepath.name}: {e}", file=sys.stderr)
        return None


def extract_cover_art(filepath: Path, output_dir: Path) -> Optional[str]:
    """Extract cover art from audiobook file."""
    try:
        file_hash = hashlib.md5(str(filepath).encode()).hexdigest()
        cover_path = output_dir / f"{file_hash}.jpg"

        if cover_path.exists():
            return cover_path.name

        cmd = [
            "ffmpeg", "-v", "quiet", "-i", str(filepath),
            "-an", "-vcodec", "copy", str(cover_path),
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and cover_path.exists():
            return cover_path.name
        return None
    except Exception:
        return None


def categorize_genre(genre: str) -> dict:
    """Categorize genre into main/sub categories."""
    genre_lower = genre.lower()

    categories = {
        "fiction": {
            "mystery & thriller": ["mystery", "thriller", "crime", "detective", "noir", "suspense"],
            "science fiction": ["science fiction", "sci-fi", "scifi", "cyberpunk", "space opera"],
            "fantasy": ["fantasy", "epic fantasy", "urban fantasy", "magical realism"],
            "literary fiction": ["literary", "contemporary", "historical fiction"],
            "horror": ["horror", "supernatural", "gothic"],
            "romance": ["romance", "romantic"],
        },
        "non-fiction": {
            "biography & memoir": ["biography", "memoir", "autobiography"],
            "history": ["history", "historical"],
            "science": ["science", "physics", "biology", "chemistry", "astronomy"],
            "philosophy": ["philosophy", "ethics"],
            "self-help": ["self-help", "personal development", "psychology"],
            "business": ["business", "economics", "entrepreneurship"],
            "true crime": ["true crime"],
        },
    }

    for main_cat, subcats in categories.items():
        for subcat, keywords in subcats.items():
            if any(keyword in genre_lower for keyword in keywords):
                return {"main": main_cat, "sub": subcat, "original": genre}

    return {"main": "uncategorized", "sub": "general", "original": genre}


def determine_literary_era(year_str: str) -> str:
    """Determine literary era from publication year."""
    try:
        year = int(year_str[:4]) if year_str else 0

        if year == 0:
            return "Unknown Era"
        elif year < 1800:
            return "Classical (Pre-1800)"
        elif year < 1900:
            return "19th Century (1800-1899)"
        elif year < 1950:
            return "Early 20th Century (1900-1949)"
        elif year < 2000:
            return "Late 20th Century (1950-1999)"
        elif year < 2010:
            return "21st Century - Early (2000-2009)"
        elif year < 2020:
            return "21st Century - Modern (2010-2019)"
        else:
            return "21st Century - Contemporary (2020+)"
    except (ValueError, TypeError):
        return "Unknown Era"


def extract_topics(description: str) -> list[str]:
    """Extract topics from description."""
    description_lower = description.lower()
    topics = []

    topic_keywords = {
        "war": ["war", "battle", "military", "conflict"],
        "adventure": ["adventure", "journey", "quest", "expedition"],
        "technology": ["technology", "computer", "ai", "artificial intelligence"],
        "politics": ["politics", "political", "government", "election"],
        "religion": ["religion", "faith", "spiritual", "god"],
        "family": ["family", "parent", "child", "marriage"],
        "society": ["society", "social", "culture", "community"],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in description_lower for kw in keywords):
            topics.append(topic)

    return topics if topics else ["general"]


def insert_audiobook(conn: sqlite3.Connection, metadata: dict, cover_path: Optional[str]) -> Optional[int]:
    """Insert a single audiobook into the database. Returns the new ID."""
    cursor = conn.cursor()

    # Insert main record
    cursor.execute("""
        INSERT INTO audiobooks (
            title, author, narrator, publisher, series,
            duration_hours, duration_formatted, file_size_mb,
            file_path, cover_path, format, description,
            sha256_hash, hash_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metadata.get("title"),
        metadata.get("author"),
        metadata.get("narrator"),
        metadata.get("publisher"),
        metadata.get("series"),
        metadata.get("duration_hours"),
        metadata.get("duration_formatted"),
        metadata.get("file_size_mb"),
        metadata.get("file_path"),
        cover_path,
        metadata.get("format"),
        metadata.get("description", ""),
        metadata.get("sha256_hash"),
        metadata.get("hash_verified_at"),
    ))

    audiobook_id = cursor.lastrowid

    # Insert genre
    genre = metadata.get("genre", "Uncategorized")
    genre_cat = categorize_genre(genre)

    # Get or create genre
    cursor.execute("SELECT id FROM genres WHERE name = ?", (genre_cat["sub"],))
    row = cursor.fetchone()
    if row:
        genre_id = row[0]
    else:
        cursor.execute("INSERT INTO genres (name) VALUES (?)", (genre_cat["sub"],))
        genre_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
        (audiobook_id, genre_id)
    )

    # Insert era
    era = determine_literary_era(metadata.get("year", ""))
    cursor.execute("SELECT id FROM eras WHERE name = ?", (era,))
    row = cursor.fetchone()
    if row:
        era_id = row[0]
    else:
        cursor.execute("INSERT INTO eras (name) VALUES (?)", (era,))
        era_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)",
        (audiobook_id, era_id)
    )

    # Insert topics
    topics = extract_topics(metadata.get("description", ""))
    for topic_name in topics:
        cursor.execute("SELECT id FROM topics WHERE name = ?", (topic_name,))
        row = cursor.fetchone()
        if row:
            topic_id = row[0]
        else:
            cursor.execute("INSERT INTO topics (name) VALUES (?)", (topic_name,))
            topic_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO audiobook_topics (audiobook_id, topic_id) VALUES (?, ?)",
            (audiobook_id, topic_id)
        )

    return audiobook_id


def add_new_audiobooks(
    library_dir: Path = AUDIOBOOK_DIR,
    db_path: Path = DATABASE_PATH,
    cover_dir: Path = COVER_DIR,
    calculate_hashes: bool = True,
    progress_callback: ProgressCallback = None
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
        return {"added": added_count, "skipped": skipped_count, "errors": errors_count, "new_files": new_files_list}

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
                progress_callback(pct, 100, f"Processing {idx}/{total}: {filepath.name}")

            print(f"[{idx:3d}/{total}] Adding: {filepath.name}")

            # Extract metadata
            metadata = get_file_metadata(filepath, calculate_hash=calculate_hashes)
            if not metadata:
                errors_count += 1
                continue

            # Extract cover art
            cover_path = extract_cover_art(filepath, cover_dir)

            try:
                # Insert into database
                audiobook_id = insert_audiobook(conn, metadata, cover_path)
                conn.commit()

                added_count += 1
                new_files_list.append({
                    "id": audiobook_id,
                    "title": metadata.get("title"),
                    "author": metadata.get("author"),
                    "file_path": str(filepath),
                })

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

    return {"added": added_count, "skipped": skipped_count, "errors": errors_count, "new_files": new_files_list}


def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Add new audiobooks to database (incremental scan)"
    )
    parser.add_argument(
        "--no-hash", action="store_true",
        help="Skip SHA-256 hash calculation (faster but no integrity verification)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be added without actually adding"
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
    results = add_new_audiobooks(
        calculate_hashes=not args.no_hash
    )

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
