#!/usr/bin/env python3
"""
Embed cover art into Opus audiobook files using mutagen.

This script finds all .opus files in the Library directory and embeds
the corresponding cover image using METADATA_BLOCK_PICTURE Vorbis comments.

Usage:
    python3 embed-cover-art.py [--dry-run] [--parallel N]

Configuration is read from environment variables:
    AUDIOBOOKS_LIBRARY - Path to audiobook library directory
    AUDIOBOOKS_LOGS    - Path to log directory
"""

import argparse
import base64
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple

try:
    from mutagen.oggopus import OggOpus
    from mutagen.flac import Picture
except ImportError:
    print("Error: mutagen is required. Install with: pip3 install mutagen")
    sys.exit(1)

# Configuration from environment with defaults
LIBRARY_DIR = os.environ.get('AUDIOBOOKS_LIBRARY', '/srv/audiobooks/Library')
LOG_DIR = os.environ.get('AUDIOBOOKS_LOGS', '/var/log/audiobooks')
LOG_FILE = os.path.join(LOG_DIR, 'embed_cover_art.log')


def find_cover_image(opus_path: Path) -> Optional[Path]:
    """Find a cover image in the same directory as the opus file."""
    directory = opus_path.parent
    opus_stem = opus_path.stem

    # Try exact match first (same name as opus file)
    for ext in ['.jpg', '.jpeg', '.png']:
        cover_path = directory / f"{opus_stem}{ext}"
        if cover_path.exists():
            return cover_path

    # Try common cover names
    for name in ['cover', 'Cover', 'folder', 'Folder']:
        for ext in ['.jpg', '.jpeg', '.png']:
            cover_path = directory / f"{name}{ext}"
            if cover_path.exists():
                return cover_path

    # Find any image file in directory
    for ext in ['*.jpg', '*.jpeg', '*.png']:
        images = list(directory.glob(ext))
        if images:
            return images[0]

    return None


def has_embedded_cover(opus_path: Path) -> bool:
    """Check if opus file already has embedded cover art."""
    try:
        audio = OggOpus(str(opus_path))
        return 'metadata_block_picture' in (audio.tags or {})
    except Exception:
        return False


def get_mime_type(cover_path: Path) -> str:
    """Determine MIME type from file extension."""
    ext = cover_path.suffix.lower()
    if ext in ['.jpg', '.jpeg']:
        return 'image/jpeg'
    elif ext == '.png':
        return 'image/png'
    else:
        return 'image/jpeg'  # Default to JPEG


def embed_cover(opus_path: Path, cover_path: Path, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Embed cover art into opus file using mutagen.

    Returns:
        Tuple of (success: bool, message: str)
    """
    if dry_run:
        return True, f"DRY RUN: Would embed {cover_path.name} into {opus_path.name}"

    try:
        # Load opus file
        audio = OggOpus(str(opus_path))

        # Create Picture object
        picture = Picture()
        picture.type = 3  # Front cover
        picture.mime = get_mime_type(cover_path)
        picture.desc = 'Front cover'

        # Read image data
        with open(cover_path, 'rb') as f:
            picture.data = f.read()

        # Encode to base64 for Vorbis comment
        picture_data = picture.write()
        encoded_data = base64.b64encode(picture_data).decode('ascii')

        # Add to tags
        audio['metadata_block_picture'] = [encoded_data]
        audio.save()

        return True, f"Embedded {cover_path.name} ({len(picture.data)} bytes)"

    except Exception as e:
        return False, f"Error: {str(e)}"


def process_file(opus_path: Path, dry_run: bool = False) -> Tuple[str, bool, str]:
    """Process a single opus file."""
    # Skip if already has cover
    if has_embedded_cover(opus_path):
        return str(opus_path), True, "Already has embedded cover"

    # Find cover image
    cover_path = find_cover_image(opus_path)
    if cover_path is None:
        return str(opus_path), False, "No cover image found"

    # Embed cover
    success, message = embed_cover(opus_path, cover_path, dry_run)
    return str(opus_path), success, message


def main():
    parser = argparse.ArgumentParser(description='Embed cover art into Opus audiobook files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    parser.add_argument('--parallel', type=int, default=8, help='Number of parallel workers (default: 8)')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of files to process (0 = no limit)')
    parser.add_argument('--dir', type=str, default=LIBRARY_DIR, help=f'Directory to process (default: {LIBRARY_DIR})')
    args = parser.parse_args()

    library_dir = Path(args.dir)
    if not library_dir.exists():
        print(f"Error: Directory not found: {library_dir}")
        sys.exit(1)

    # Find all opus files
    print(f"Scanning {library_dir} for opus files...")
    opus_files = list(library_dir.rglob('*.opus'))

    if args.limit > 0:
        opus_files = opus_files[:args.limit]

    total = len(opus_files)
    print(f"Found {total} opus files")

    if args.dry_run:
        print("DRY RUN MODE - no changes will be made")

    print(f"Processing with {args.parallel} parallel workers...")
    print()

    # Track results
    success_count = 0
    skip_count = 0
    fail_count = 0
    failed_files = []

    # Process files in parallel
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(process_file, opus_path, args.dry_run): opus_path
            for opus_path in opus_files
        }

        for i, future in enumerate(as_completed(futures), 1):
            opus_path = futures[future]
            try:
                path, success, message = future.result()

                if success:
                    if "Already" in message or "DRY RUN" in message:
                        skip_count += 1
                        status = "SKIP"
                    else:
                        success_count += 1
                        status = "OK"
                else:
                    fail_count += 1
                    status = "FAIL"
                    failed_files.append((path, message))

                # Progress output
                filename = Path(path).name
                if len(filename) > 50:
                    filename = filename[:47] + "..."
                print(f"[{i}/{total}] {status}: {filename} - {message}")

            except Exception as e:
                fail_count += 1
                failed_files.append((str(opus_path), str(e)))
                print(f"[{i}/{total}] FAIL: {opus_path.name} - {e}")

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total files:     {total}")
    print(f"Embedded:        {success_count}")
    print(f"Skipped:         {skip_count}")
    print(f"Failed:          {fail_count}")

    if failed_files:
        print()
        print("Failed files:")
        for path, message in failed_files[:20]:
            print(f"  {Path(path).name}: {message}")
        if len(failed_files) > 20:
            print(f"  ... and {len(failed_files) - 20} more")

    # Log results
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    with open(LOG_FILE, 'a') as f:
        from datetime import datetime
        f.write(f"\n{'=' * 60}\n")
        f.write(f"Run: {datetime.now().isoformat()}\n")
        f.write(f"Total: {total}, Embedded: {success_count}, Skipped: {skip_count}, Failed: {fail_count}\n")
        if failed_files:
            f.write("Failed files:\n")
            for path, message in failed_files:
                f.write(f"  {path}: {message}\n")


if __name__ == '__main__':
    main()
