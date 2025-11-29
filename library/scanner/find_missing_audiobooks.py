#!/usr/bin/env python3
"""
Find Missing/Corrupted Audiobooks
Identifies empty or corrupted audiobook files that need to be re-downloaded
"""

import json
from pathlib import Path
import csv

AUDIOBOOK_DIR = Path("/raid0/Audiobooks")
OUTPUT_CSV = Path("missing_audiobooks.csv")
OUTPUT_TXT = Path("missing_audiobooks.txt")

def find_corrupted_files():
    """Find all empty or corrupted audiobook files"""
    corrupted = []

    # Find all audiobook files
    for ext in ['.m4b', '.opus', '.m4a', '.mp3', '.aaxc']:
        for filepath in AUDIOBOOK_DIR.rglob(f"*{ext}"):
            # Check if file is empty (0 bytes)
            if filepath.stat().st_size == 0:
                # Extract title from filename
                title = filepath.stem

                # Try to clean up the title
                title_clean = title.replace('_', ' ')

                # Remove quality indicators
                for quality in ['-AAX 44 128', '-AAX 22 64', '-AAX_44_128', '-AAX_22_64']:
                    title_clean = title_clean.replace(quality, '')

                corrupted.append({
                    'title': title_clean.strip(),
                    'filename': filepath.name,
                    'path': str(filepath.relative_to(AUDIOBOOK_DIR.parent)),
                    'directory': filepath.parent.name,
                    'extension': ext
                })

    return corrupted

def main():
    print("Scanning for corrupted/empty audiobook files...")
    print()

    corrupted = find_corrupted_files()

    if not corrupted:
        print("✓ No corrupted files found! All audiobooks are valid.")
        return

    # Sort by title
    corrupted.sort(key=lambda x: x['title'].lower())

    print(f"Found {len(corrupted)} corrupted/empty audiobook files")
    print()

    # Group by directory
    by_directory = {}
    for item in corrupted:
        dir_name = item['directory']
        if dir_name not in by_directory:
            by_directory[dir_name] = []
        by_directory[dir_name].append(item)

    print("Breakdown by directory:")
    for dir_name, items in sorted(by_directory.items()):
        print(f"  {dir_name}: {len(items)} files")
    print()

    # Save to CSV
    print(f"Saving list to {OUTPUT_CSV}...")
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['title', 'filename', 'directory', 'extension', 'path'])
        writer.writeheader()
        writer.writerows(corrupted)

    # Save to text file (easier to read)
    print(f"Saving list to {OUTPUT_TXT}...")
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write("MISSING/CORRUPTED AUDIOBOOKS\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total: {len(corrupted)} audiobooks need to be re-downloaded\n\n")
        f.write("INSTRUCTIONS:\n")
        f.write("1. Log in to your Audible account\n")
        f.write("2. Go to your Library\n")
        f.write("3. Search for each title below\n")
        f.write("4. Download the audiobook file\n")
        f.write("5. Run the scanner again to update your library\n\n")
        f.write("=" * 80 + "\n\n")

        # Group by directory for readability
        for dir_name, items in sorted(by_directory.items()):
            f.write(f"\n{'=' * 80}\n")
            f.write(f"DIRECTORY: {dir_name} ({len(items)} files)\n")
            f.write(f"{'=' * 80}\n\n")

            for idx, item in enumerate(items, 1):
                f.write(f"{idx}. {item['title']}\n")
                f.write(f"   File: {item['filename']}\n")
                f.write(f"   Path: {item['path']}\n")
                f.write("\n")

    # Display sample
    print()
    print("=" * 80)
    print("SAMPLE OF MISSING AUDIOBOOKS (first 20):")
    print("=" * 80)
    for idx, item in enumerate(corrupted[:20], 1):
        print(f"{idx}. {item['title']}")
        print(f"   Directory: {item['directory']}")
        print()

    if len(corrupted) > 20:
        print(f"... and {len(corrupted) - 20} more")
        print()

    print("=" * 80)
    print("COMPLETE LIST SAVED TO:")
    print(f"  • {OUTPUT_CSV.absolute()} (CSV format)")
    print(f"  • {OUTPUT_TXT.absolute()} (Text format)")
    print("=" * 80)
    print()
    print("TIP: You can import the CSV file into a spreadsheet to track")
    print("     which audiobooks you've re-downloaded.")
    print()

if __name__ == '__main__':
    main()
