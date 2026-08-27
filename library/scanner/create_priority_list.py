#!/usr/bin/env python3
"""Create a priority list of audiobooks that genuinely need RE-DOWNLOADING.

Reads missing_audiobooks.csv and keeps only rows whose ``remedy`` is
``re-download``. Derived artifacts — cover art and chapter files under
``translated/`` — are REGENERATED from source, not re-downloaded, so listing
them here produces an instruction that cannot be carried out
(Audiobook-Manager-aw9).
"""

import csv
import sys
from pathlib import Path

# Add parent directory to path for scanner imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from scanner.utils.constants import is_cover_art_file

INPUT_CSV = Path("missing_audiobooks.csv")
OUTPUT_TXT = Path("priority_audiobooks_to_redownload.txt")


def main():
    # Read CSV and filter out cover files
    priority_books = []

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter on the remedy the upstream scanner already computed rather
            # than re-deriving "is this a real audiobook" here. This list says
            # RE-DOWNLOAD, and only source media can be re-downloaded — chapter
            # artifacts under translated/ are REGENERATED from source.
            #
            # This used to filter cover art only, so a zero-byte
            # translated/<book>.zh-Hans.opus was listed as an audiobook to fetch
            # from Audible — an instruction that cannot be carried out. The
            # remedy column (Audiobook-Manager-aw9) makes the distinction
            # available instead of guessable. Falls back to the old cover-only
            # rule when the column is absent, so a CSV written by an older
            # scanner still parses.
            remedy = row.get("remedy")
            if remedy is not None:
                if remedy != "re-download":
                    continue
            elif is_cover_art_file(row["filename"]):
                continue
            priority_books.append(row)

    # Write priority list
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("PRIORITY: ACTUAL AUDIOBOOKS NEEDING RE-DOWNLOAD\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total: {len(priority_books)} audiobook files needing re-download\n\n")
        f.write("INSTRUCTIONS:\n")
        f.write("1. Log in to your Audible account at audible.com\n")
        f.write("2. Go to your Library\n")
        f.write("3. Search for each title below\n")
        f.write("4. Download the audiobook file\n")
        f.write("5. Run the scanner again to update your library\n\n")
        f.write("NOTE: This list contains ONLY files whose remedy is re-download.\n")
        f.write("      Derived artifacts (cover art, translated/ chapters) are\n")
        f.write("      REGENERATED, not re-downloaded, and are listed separately in\n")
        f.write("      missing_audiobooks.txt under REGENERATE.\n\n")
        f.write("=" * 80 + "\n\n")

        # Group by directory for organization
        by_directory: dict[str, list[dict]] = {}
        for book in priority_books:
            dir_name = book["directory"]
            if dir_name not in by_directory:
                by_directory[dir_name] = []
            by_directory[dir_name].append(book)

        for dir_name, items in sorted(by_directory.items()):
            f.write(f"\n{'=' * 80}\n")
            plural = "s" if len(items) > 1 else ""
            f.write(f"DIRECTORY: {dir_name} ({len(items)} file{plural})\n")
            f.write(f"{'=' * 80}\n\n")

            for idx, item in enumerate(items, 1):
                f.write(f"{idx}. {item['title']}\n")
                f.write(f"   File: {item['filename']}\n")
                f.write(f"   Path: {item['path']}\n")
                f.write("\n")

    print(f"Created priority list with {len(priority_books)} actual audiobook files")
    print(f"Output: {OUTPUT_TXT.absolute()}")


if __name__ == "__main__":
    main()
