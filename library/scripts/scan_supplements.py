#!/usr/bin/env python3
"""
Scan supplements directory and import to database.
Matches supplement files to audiobooks by title.

Usage:
    python3 scan_supplements.py [--supplements-dir /path/to/supplements]
"""

import sqlite3
import sys
from pathlib import Path

# Add parent for config import
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from config import DATABASE_PATH, PROJECT_DIR
except ImportError:
    DATABASE_PATH = Path('/app/data/audiobooks.db')
    PROJECT_DIR = Path('/app')

# Default supplements directory
DEFAULT_SUPPLEMENTS_DIR = Path('/raid0/Audiobooks/Supplements')


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_supplements_table(cursor):
    """Ensure supplements table exists"""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS supplements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER,
            asin TEXT,
            type TEXT NOT NULL DEFAULT 'pdf',
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size_mb REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplements_audiobook_id ON supplements(audiobook_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplements_asin ON supplements(asin)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_supplements_type ON supplements(type)")


def scan_supplements(supplements_dir: Path, verbose: bool = True):
    """Scan supplements directory and update database"""
    if not supplements_dir.exists():
        print(f"Supplements directory not found: {supplements_dir}")
        return {'added': 0, 'updated': 0, 'skipped': 0}

    conn = get_db()
    cursor = conn.cursor()

    # Ensure table exists
    ensure_supplements_table(cursor)
    conn.commit()

    # Get existing supplements
    cursor.execute("SELECT file_path FROM supplements")
    existing_paths = {row['file_path'] for row in cursor.fetchall()}

    added = 0
    updated = 0
    skipped = 0

    # Type mapping
    type_map = {
        'pdf': 'pdf',
        'epub': 'ebook',
        'mobi': 'ebook',
        'jpg': 'image',
        'jpeg': 'image',
        'png': 'image',
        'mp3': 'audio',
        'wav': 'audio',
    }

    for file_path in supplements_dir.iterdir():
        if not file_path.is_file():
            continue

        path_str = str(file_path)
        filename = file_path.name
        ext = file_path.suffix.lower().lstrip('.')

        # Skip non-supplement files
        if ext not in type_map:
            skipped += 1
            continue

        file_size = file_path.stat().st_size / (1024 * 1024)  # MB
        supplement_type = type_map.get(ext, 'other')

        # Clean filename for matching
        clean_name = file_path.stem.replace('_', ' ').replace('-', ' ')

        # Try to match to an audiobook by title (first 30 chars)
        cursor.execute("""
            SELECT id, title FROM audiobooks
            WHERE LOWER(title) LIKE ?
            OR LOWER(REPLACE(REPLACE(title, ':', ''), '-', '')) LIKE ?
            LIMIT 1
        """, (f"%{clean_name[:30].lower()}%", f"%{clean_name[:30].lower()}%"))

        match = cursor.fetchone()
        audiobook_id = match['id'] if match else None

        if path_str in existing_paths:
            # Update existing record
            cursor.execute("""
                UPDATE supplements
                SET audiobook_id = ?, file_size_mb = ?, type = ?
                WHERE file_path = ?
            """, (audiobook_id, file_size, supplement_type, path_str))
            updated += 1
            if verbose:
                status = f"linked to '{match['title'][:40]}...'" if match else "unlinked"
                print(f"  Updated: {filename[:50]} ({status})")
        else:
            # Insert new record
            cursor.execute("""
                INSERT INTO supplements (audiobook_id, type, filename, file_path, file_size_mb)
                VALUES (?, ?, ?, ?, ?)
            """, (audiobook_id, supplement_type, filename, path_str, file_size))
            added += 1
            if verbose:
                status = f"linked to '{match['title'][:40]}...'" if match else "unlinked"
                print(f"  Added: {filename[:50]} ({status})")

    conn.commit()
    conn.close()

    return {'added': added, 'updated': updated, 'skipped': skipped}


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Scan supplements directory and import to database')
    parser.add_argument('--supplements-dir', '-d', type=Path, default=DEFAULT_SUPPLEMENTS_DIR,
                        help='Path to supplements directory')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress verbose output')
    args = parser.parse_args()

    print(f"Scanning supplements from: {args.supplements_dir}")
    print(f"Database: {DATABASE_PATH}")
    print()

    results = scan_supplements(args.supplements_dir, verbose=not args.quiet)

    print()
    print("=" * 40)
    print(f"Added: {results['added']}")
    print(f"Updated: {results['updated']}")
    print(f"Skipped: {results['skipped']}")
    print("=" * 40)


if __name__ == '__main__':
    main()
