#!/usr/bin/env python3
"""
Update audiobook metadata from source AAXC files
Extracts narrator, publisher, and description using mediainfo
Updates database without re-converting files
"""

import sqlite3
import subprocess
import sys
import os
import re
from pathlib import Path
from difflib import SequenceMatcher

# Paths
DB_PATH = Path("/raid0/ClaudeCodeProjects/Audiobooks/library/backend/audiobooks.db")
SOURCES_DIR = Path("/raid0/Audiobooks/Sources")

def normalize_title(title):
    """Normalize title for matching (remove special chars, lowercase, etc.)"""
    # Remove common suffixes
    title = re.sub(r'-AAX_\d+_\d+$', '', title)
    title = re.sub(r'_\(\d+\)$', '', title)  # Remove trailing _(1215) etc.
    title = re.sub(r'^B[A-Z0-9]+_', '', title)  # Remove ASIN prefix

    # Replace underscores with spaces
    title = title.replace('_', ' ')

    # Remove special characters but keep spaces
    title = re.sub(r'[^\w\s]', '', title)

    # Normalize whitespace
    title = ' '.join(title.split())

    return title.lower().strip()


def find_source_file(book_title, book_path):
    """Find the source AAXC file for a given book"""
    # Try direct match first
    book_basename = Path(book_path).stem

    # Look for AAXC files
    aaxc_files = list(SOURCES_DIR.glob("*.aaxc"))

    if not aaxc_files:
        return None

    # Normalize book title for matching
    norm_book_title = normalize_title(book_basename)

    # First pass: Try exact prefix matching (handles cases where AAXC has subtitle)
    for aaxc_file in aaxc_files:
        aaxc_basename = aaxc_file.stem
        norm_aaxc_title = normalize_title(aaxc_basename)

        # Check if AAXC title starts with the book title
        if norm_aaxc_title.startswith(norm_book_title):
            return aaxc_file

    # Second pass: Fuzzy matching for edge cases
    best_match = None
    best_ratio = 0.0

    for aaxc_file in aaxc_files:
        # Normalize AAXC filename
        aaxc_basename = aaxc_file.stem
        norm_aaxc_title = normalize_title(aaxc_basename)

        # Calculate similarity
        ratio = SequenceMatcher(None, norm_book_title, norm_aaxc_title).ratio()

        if ratio > best_ratio:
            best_ratio = ratio
            best_match = aaxc_file

    # Only return if similarity is reasonably high
    if best_ratio >= 0.6:  # 60% similarity threshold
        return best_match

    return None


def extract_metadata_from_source(aaxc_file):
    """Extract metadata from AAXC file using mediainfo and ffprobe"""
    metadata = {
        'narrator': None,
        'publisher': None,
        'description': None,
        'series': None,
        'genre': None,
        'published_year': None
    }

    # Extract narrator using mediainfo
    try:
        result = subprocess.run(
            ['mediainfo', '--Inform=General;%nrt%', str(aaxc_file)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            metadata['narrator'] = result.stdout.strip()
    except Exception as e:
        print(f"  Warning: Could not extract narrator: {e}", file=sys.stderr)

    # Extract publisher using mediainfo
    try:
        result = subprocess.run(
            ['mediainfo', '--Inform=General;%pub%', str(aaxc_file)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            metadata['publisher'] = result.stdout.strip()
    except Exception as e:
        print(f"  Warning: Could not extract publisher: {e}", file=sys.stderr)

    # Extract description using mediainfo
    try:
        result = subprocess.run(
            ['mediainfo', '--Inform=General;%Track_More%', str(aaxc_file)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            description = result.stdout.strip()
            # Limit to reasonable length (5000 chars)
            if len(description) > 5000:
                description = description[:5000] + "..."
            metadata['description'] = description
    except Exception as e:
        print(f"  Warning: Could not extract description: {e}", file=sys.stderr)

    # Extract genre, date, series using ffprobe
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', str(aaxc_file)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            tags = data.get('format', {}).get('tags', {})

            # Normalize tag keys
            tags_norm = {k.lower(): v for k, v in tags.items()}

            # Extract genre
            if 'genre' in tags_norm:
                metadata['genre'] = tags_norm['genre']

            # Extract year
            if 'date' in tags_norm:
                year_str = tags_norm['date']
                # Extract 4-digit year
                year_match = re.search(r'\d{4}', year_str)
                if year_match:
                    metadata['published_year'] = int(year_match.group())

            # Extract series
            if 'series' in tags_norm:
                metadata['series'] = tags_norm['series']

    except Exception as e:
        print(f"  Warning: Could not extract ffprobe metadata: {e}", file=sys.stderr)

    return metadata


def update_database():
    """Main function to update database with metadata from source files"""
    print("="*70)
    print("AUDIOBOOK METADATA UPDATE FROM SOURCE FILES")
    print("="*70)
    print()

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all audiobooks
    cursor.execute("""
        SELECT id, title, author, narrator, publisher, file_path, description
        FROM audiobooks
        ORDER BY id
    """)

    books = cursor.fetchall()
    total_books = len(books)

    print(f"Total books in database: {total_books}")
    print(f"Source directory: {SOURCES_DIR}")
    print()

    # Statistics
    stats = {
        'processed': 0,
        'source_found': 0,
        'source_not_found': 0,
        'narrator_updated': 0,
        'publisher_updated': 0,
        'description_updated': 0,
        'genre_updated': 0,
        'year_updated': 0,
        'series_updated': 0,
        'errors': 0
    }

    for idx, book in enumerate(books, 1):
        book_id = book['id']
        title = book['title']
        current_narrator = book['narrator']
        current_publisher = book['publisher']
        file_path = book['file_path']

        print(f"[{idx}/{total_books}] Processing: {title}")

        # Find source file
        source_file = find_source_file(title, file_path)

        if not source_file:
            print(f"  ⚠ Source file not found")
            stats['source_not_found'] += 1
            stats['processed'] += 1
            continue

        print(f"  ✓ Found source: {source_file.name}")
        stats['source_found'] += 1

        # Extract metadata
        try:
            metadata = extract_metadata_from_source(source_file)

            # Prepare update fields
            updates = []
            params = []

            # Update narrator if missing or "Unknown Narrator"
            if metadata['narrator'] and (not current_narrator or current_narrator == 'Unknown Narrator'):
                updates.append("narrator = ?")
                params.append(metadata['narrator'])
                stats['narrator_updated'] += 1
                print(f"  → Narrator: {metadata['narrator']}")

            # Update publisher if missing or "Unknown Publisher"
            if metadata['publisher'] and (not current_publisher or current_publisher == 'Unknown Publisher'):
                updates.append("publisher = ?")
                params.append(metadata['publisher'])
                stats['publisher_updated'] += 1
                print(f"  → Publisher: {metadata['publisher']}")

            # Update description if missing or empty
            if metadata['description'] and (not book['description'] or not book['description'].strip()):
                updates.append("description = ?")
                params.append(metadata['description'])
                stats['description_updated'] += 1
                print(f"  → Description: {metadata['description'][:60]}...")

            # Note: Genre is in a separate table, skip for now

            # Update year if available
            if metadata['published_year']:
                updates.append("published_year = ?")
                params.append(metadata['published_year'])
                stats['year_updated'] += 1
                print(f"  → Year: {metadata['published_year']}")

            # Update series if available
            if metadata['series']:
                updates.append("series = ?")
                params.append(metadata['series'])
                stats['series_updated'] += 1
                print(f"  → Series: {metadata['series']}")

            # Execute update if there are changes
            if updates:
                params.append(book_id)
                update_query = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"

                # Debug: print query and param count
                # print(f"  DEBUG: Query: {update_query}")
                # print(f"  DEBUG: Params ({len(params)}): {params}")

                try:
                    cursor.execute(update_query, params)
                    conn.commit()
                    print(f"  ✓ Updated {len(updates)} fields")
                except sqlite3.Error as sql_err:
                    print(f"  ✗ SQL Error: {sql_err}")
                    print(f"  Query: {update_query}")
                    print(f"  Params ({len(params)}): {params}")
                    stats['errors'] += 1
            else:
                print(f"  - No updates needed")

        except Exception as e:
            print(f"  ✗ Error: {e}")
            stats['errors'] += 1

        stats['processed'] += 1
        print()

    # Close database
    conn.close()

    # Print summary
    print()
    print("="*70)
    print("UPDATE COMPLETE")
    print("="*70)
    print(f"Total books processed: {stats['processed']}")
    print(f"Source files found: {stats['source_found']}")
    print(f"Source files not found: {stats['source_not_found']}")
    print()
    print("Updates:")
    print(f"  Narrators updated: {stats['narrator_updated']}")
    print(f"  Publishers updated: {stats['publisher_updated']}")
    print(f"  Descriptions updated: {stats['description_updated']}")
    print(f"  Years updated: {stats['year_updated']}")
    print(f"  Series updated: {stats['series_updated']}")
    print()
    print(f"Errors: {stats['errors']}")
    print("="*70)


if __name__ == '__main__':
    update_database()
