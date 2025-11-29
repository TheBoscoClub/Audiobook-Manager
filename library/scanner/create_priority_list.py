#!/usr/bin/env python3
"""
Create a priority list of actual audiobooks (excluding cover files) that need re-downloading
"""

import csv
from pathlib import Path

INPUT_CSV = Path("missing_audiobooks.csv")
OUTPUT_TXT = Path("priority_audiobooks_to_redownload.txt")

def main():
    # Read CSV and filter out cover files
    priority_books = []

    with open(INPUT_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip cover files
            if '.cover.' not in row['filename']:
                priority_books.append(row)

    # Write priority list
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write("PRIORITY: ACTUAL AUDIOBOOKS NEEDING RE-DOWNLOAD\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total: {len(priority_books)} audiobook files (excludes cover image files)\n\n")
        f.write("INSTRUCTIONS:\n")
        f.write("1. Log in to your Audible account at audible.com\n")
        f.write("2. Go to your Library\n")
        f.write("3. Search for each title below\n")
        f.write("4. Download the audiobook file\n")
        f.write("5. Run the scanner again to update your library\n\n")
        f.write("NOTE: This list excludes .cover files (images) which are non-critical.\n")
        f.write("      The full list including covers is in missing_audiobooks.txt\n\n")
        f.write("=" * 80 + "\n\n")

        # Group by directory for organization
        by_directory = {}
        for book in priority_books:
            dir_name = book['directory']
            if dir_name not in by_directory:
                by_directory[dir_name] = []
            by_directory[dir_name].append(book)

        for dir_name, items in sorted(by_directory.items()):
            f.write(f"\n{'=' * 80}\n")
            f.write(f"DIRECTORY: {dir_name} ({len(items)} file{'s' if len(items) > 1 else ''})\n")
            f.write(f"{'=' * 80}\n\n")

            for idx, item in enumerate(items, 1):
                f.write(f"{idx}. {item['title']}\n")
                f.write(f"   File: {item['filename']}\n")
                f.write(f"   Path: {item['path']}\n")
                f.write("\n")

    print(f"Created priority list with {len(priority_books)} actual audiobook files")
    print(f"Output: {OUTPUT_TXT.absolute()}")

if __name__ == '__main__':
    main()
