#!/usr/bin/env python3
"""
Update audiobook metadata from source AAXC files
Extracts narrator, publisher, and description using mediainfo
Updates database without re-converting files
"""

import re
import sqlite3
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOKS_SOURCES, DATABASE_PATH

# Paths - use config
DB_PATH = DATABASE_PATH
SOURCES_DIR = AUDIOBOOKS_SOURCES


def normalize_source_filename(title):
    """Normalize title for matching (remove special chars, lowercase, etc.)"""
    # Remove common suffixes
    title = re.sub(r"-AAX_\d+_\d+$", "", title)
    title = re.sub(r"_\(\d+\)$", "", title)  # Remove trailing _(1215) etc.
    title = re.sub(r"^B[A-Z0-9]+_", "", title)  # Remove ASIN prefix

    # Replace underscores with spaces
    title = title.replace("_", " ")

    # Remove special characters but keep spaces
    title = re.sub(r"[^\w\s]", "", title)

    # Normalize whitespace
    title = " ".join(title.split())

    return title.lower().strip()


def find_source_file(_book_title, book_path):
    """Find the source AAXC file for a given book.

    book_title is retained for API compatibility with call sites but unused —
    matching is done against the file basename and fuzzy source filename.
    """
    book_basename = Path(book_path).stem

    # Look for AAXC files
    aaxc_files = list(SOURCES_DIR.glob("*.aaxc"))

    if not aaxc_files:
        return None

    # Normalize book title for matching
    norm_book_title = normalize_source_filename(book_basename)

    # First pass: Try exact prefix matching (handles cases where AAXC has subtitle)
    for aaxc_file in aaxc_files:
        aaxc_basename = aaxc_file.stem
        norm_aaxc_title = normalize_source_filename(aaxc_basename)

        # Check if AAXC title starts with the book title
        if norm_aaxc_title.startswith(norm_book_title):
            return aaxc_file

    # Second pass: Fuzzy matching for edge cases
    best_match = None
    best_ratio = 0.0

    for aaxc_file in aaxc_files:
        # Normalize AAXC filename
        aaxc_basename = aaxc_file.stem
        norm_aaxc_title = normalize_source_filename(aaxc_basename)

        # Calculate similarity
        ratio = SequenceMatcher(None, norm_book_title, norm_aaxc_title).ratio()

        if ratio > best_ratio:
            best_ratio = ratio
            best_match = aaxc_file

    # Only return if similarity is reasonably high
    if best_ratio >= 0.6:  # 60% similarity threshold
        return best_match

    return None


def _run_mediainfo(aaxc_file, inform_template):
    """Run mediainfo with a given --Inform template. Returns stdout or None."""
    try:
        result = subprocess.run(
            ["mediainfo", f"--Inform=General;{inform_template}", str(aaxc_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        print(f"  Warning: mediainfo extraction failed: {e}", file=sys.stderr)
    return None


def _extract_mediainfo_fields(aaxc_file, metadata):
    """Extract narrator, publisher, description via mediainfo."""
    narrator = _run_mediainfo(aaxc_file, "%nrt%")
    if narrator:
        metadata["narrator"] = narrator

    publisher = _run_mediainfo(aaxc_file, "%pub%")
    if publisher:
        metadata["publisher"] = publisher

    description = _run_mediainfo(aaxc_file, "%Track_More%")
    if description:
        if len(description) > 5000:
            description = description[:5000] + "..."
        metadata["description"] = description


def _extract_ffprobe_fields(aaxc_file, metadata):
    """Extract genre, date, series from ffprobe JSON output."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(aaxc_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return
    except Exception as e:
        print(f"  Warning: Could not extract ffprobe metadata: {e}", file=sys.stderr)
        return

    import json

    data = json.loads(result.stdout)
    tags = data.get("format", {}).get("tags", {})
    tags_norm = {k.lower(): v for k, v in tags.items()}

    if "genre" in tags_norm:
        metadata["genre"] = tags_norm["genre"]

    if "date" in tags_norm:
        year_match = re.search(r"\d{4}", tags_norm["date"])
        if year_match:
            metadata["published_year"] = int(year_match.group())

    if "series" in tags_norm:
        metadata["series"] = tags_norm["series"]


def extract_metadata_from_source(aaxc_file):
    """Extract metadata from AAXC file using mediainfo and ffprobe"""
    metadata = {
        "narrator": None,
        "publisher": None,
        "description": None,
        "series": None,
        "genre": None,
        "published_year": None,
    }

    _extract_mediainfo_fields(aaxc_file, metadata)
    _extract_ffprobe_fields(aaxc_file, metadata)

    return metadata


def _build_update_params(metadata, book):
    """Build the SQL update fields and params from extracted metadata.

    Returns (updates_list, params_list, stats_dict).
    """
    updates = []
    params = []
    stats = {}

    field_checks = [
        (
            "narrator",
            "narrator",
            lambda b: not b["narrator"] or b["narrator"] == "Unknown Narrator",
        ),
        (
            "publisher",
            "publisher",
            lambda b: not b["publisher"] or b["publisher"] == "Unknown Publisher",
        ),
        (
            "description",
            "description",
            lambda b: not b["description"] or not b["description"].strip(),
        ),
        ("published_year", "published_year", lambda _b: True),
        ("series", "series", lambda _b: True),
    ]

    for meta_key, column, should_update in field_checks:
        value = metadata[meta_key]
        if value and should_update(book):
            updates.append(f"{column} = ?")
            params.append(value)
            stats[f"{meta_key}_updated"] = 1
            label = str(value)[:60]
            if meta_key == "description":
                label = label + "..."
            print(f"  \u2192 {meta_key.title()}: {label}")

    return updates, params, stats


def _process_single_book(book, cursor, conn, stats):
    """Process a single audiobook: find source, extract metadata, update DB."""
    title = book["title"]
    file_path = book["file_path"]

    source_file = find_source_file(title, file_path)
    if not source_file:
        print("  \u26a0 Source file not found")
        stats["source_not_found"] += 1
        return

    print(f"  \u2713 Found source: {source_file.name}")
    stats["source_found"] += 1

    try:
        metadata = extract_metadata_from_source(source_file)
        updates, params, field_stats = _build_update_params(metadata, book)

        for key in field_stats:
            stats[key] = stats.get(key, 0) + 1

        if not updates:
            print("  - No updates needed")
            return

        params.append(book["id"])
        update_query = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"  # nosec B608

        try:
            cursor.execute(update_query, params)  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
            conn.commit()
            print(f"  \u2713 Updated {len(updates)} fields")
        except Exception as sql_err:
            print(f"  \u2717 SQL Error: {sql_err}")
            stats["errors"] += 1

    except Exception as e:
        print(f"  \u2717 Error: {e}")
        stats["errors"] += 1


def _print_update_summary(stats):
    """Print the metadata update summary."""
    print()
    print("=" * 70)
    print("UPDATE COMPLETE")
    print("=" * 70)
    print(f"Total books processed: {stats['processed']}")
    print(f"Source files found: {stats['source_found']}")
    print(f"Source files not found: {stats['source_not_found']}")
    print()
    print("Updates:")
    print(f"  Narrators updated: {stats.get('narrator_updated', 0)}")
    print(f"  Publishers updated: {stats.get('publisher_updated', 0)}")
    print(f"  Descriptions updated: {stats.get('description_updated', 0)}")
    print(f"  Years updated: {stats.get('published_year_updated', 0)}")
    print(f"  Series updated: {stats.get('series_updated', 0)}")
    print()
    print(f"Errors: {stats['errors']}")
    print("=" * 70)


def update_database():
    """Main function to update database with metadata from source files"""
    print("=" * 70)
    print("AUDIOBOOK METADATA UPDATE FROM SOURCE FILES")
    print("=" * 70)
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, publisher, file_path, description
        FROM audiobooks
        ORDER BY id
    """)
    books = cursor.fetchall()

    print(f"Total books in database: {len(books)}")
    print(f"Source directory: {SOURCES_DIR}")
    print()

    stats = {"processed": 0, "source_found": 0, "source_not_found": 0, "errors": 0}

    for idx, book in enumerate(books, 1):
        print(f"[{idx}/{len(books)}] Processing: {book['title']}")
        _process_single_book(book, cursor, conn, stats)
        stats["processed"] += 1
        print()

    conn.close()
    _print_update_summary(stats)


if __name__ == "__main__":
    update_database()
