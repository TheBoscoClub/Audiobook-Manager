#!/usr/bin/env python3
"""
Audiobook Metadata Scanner
Scans audiobook directory and extracts metadata from various audio formats
Supports: .m4b, .opus, .m4a, .mp3
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOK_DIR, COVER_DIR, DATA_DIR

# Import shared utilities from scanner package
from scanner.metadata_utils import (
    get_file_metadata as _get_file_metadata,
    extract_cover_art,
    enrich_metadata,
    # Re-export for backwards compatibility with tests
    categorize_genre,  # noqa: F401
    determine_literary_era,  # noqa: F401
    extract_topics,  # noqa: F401
)

# Configuration
OUTPUT_FILE = DATA_DIR / "audiobooks.json"
SUPPORTED_FORMATS = [".m4b", ".opus", ".m4a", ".mp3"]


def get_file_metadata(filepath: Path, calculate_hash: bool = True) -> dict | None:
    """Wrapper for shared get_file_metadata with AUDIOBOOK_DIR default."""
    return _get_file_metadata(filepath, AUDIOBOOK_DIR, calculate_hash)


class ProgressTracker:
    """Track progress with visual progress bar, rate calculation, and ETA."""

    # ANSI color codes
    CYAN = '\033[0;36m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color

    def __init__(self, total: int, bar_width: int = 40):
        self.total = total
        self.bar_width = bar_width
        self.start_time = time.time()
        self.current = 0
        self.last_rate_update = self.start_time
        self.last_count = 0
        self.rate = 0.0  # files per minute

    def draw_progress_bar(self, percent: int) -> str:
        """Draw a visual progress bar using Unicode block characters."""
        filled = int(percent * self.bar_width / 100)
        empty = self.bar_width - filled
        return '█' * filled + '░' * empty

    def calculate_rate_and_eta(self) -> tuple:
        """Calculate processing rate and ETA."""
        now = time.time()
        elapsed = now - self.last_rate_update

        # Update rate every 5 seconds minimum
        if elapsed >= 5 and self.current > self.last_count:
            delta = self.current - self.last_count
            self.rate = (delta * 60) / elapsed  # files per minute
            self.last_rate_update = now
            self.last_count = self.current

        remaining = self.total - self.current
        if self.rate > 0:
            eta_mins = remaining / self.rate
            if eta_mins < 1:
                eta_str = f"{int(eta_mins * 60)}s"
            elif eta_mins < 60:
                eta_str = f"{int(eta_mins)}m"
            else:
                eta_str = f"{int(eta_mins // 60)}h {int(eta_mins % 60)}m"
        else:
            eta_str = "calculating..."

        return self.rate, eta_str

    def update(self, current: int, current_file: str = ""):
        """Update progress display."""
        self.current = current
        percent = int(current * 100 / self.total) if self.total > 0 else 0
        rate, eta = self.calculate_rate_and_eta()

        # Build progress line
        bar = self.draw_progress_bar(percent)
        rate_str = f"{rate:.1f}" if rate > 0 else "..."

        # Truncate filename for display
        if current_file:
            name = current_file[:50] + "..." if len(current_file) > 50 else current_file
        else:
            name = ""

        # Print progress with carriage return for in-place update
        print(
            f"\r{self.BOLD}Progress:{self.NC} [{self.GREEN}{bar}{self.NC}] "
            f"{self.BOLD}{percent:3d}%{self.NC} | "
            f"{current}/{self.total} | "
            f"{self.CYAN}{rate_str}{self.NC} files/min | "
            f"ETA: {self.YELLOW}{eta}{self.NC}",
            end="",
            flush=True
        )

        # Print current file on next line if provided
        if name:
            # Clear line and print file info
            print(f"\n  → {name}", end="\033[A", flush=True)

    def finish(self):
        """Print final statistics."""
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            elapsed_str = f"{elapsed:.1f}s"
        elif elapsed < 3600:
            elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
        else:
            elapsed_str = f"{int(elapsed // 3600)}h {int((elapsed % 3600) // 60)}m"

        avg_rate = (self.total * 60 / elapsed) if elapsed > 0 else 0

        print()  # New line after progress bar
        print()
        print(f"{self.GREEN}{self.BOLD}✓ Scan complete!{self.NC}")
        print(f"  Total files: {self.total}")
        print(f"  Time elapsed: {elapsed_str}")
        print(f"  Average rate: {avg_rate:.1f} files/min")


# =============================================================================
# File Discovery
# =============================================================================

def find_audiobook_files(base_dir: Path, formats: list[str]) -> list[Path]:
    """
    Find all audiobook files, filtering covers and deduplicating.

    Returns list of unique audiobook file paths.
    """
    # Find all files across formats
    all_files = []
    for ext in formats:
        files = list(base_dir.rglob(f"*{ext}"))
        print(f"  Found {len(files)} {ext} files")
        all_files.extend(files)

    # Filter out cover art files
    original_count = len(all_files)
    audiobook_files = [f for f in all_files if ".cover." not in f.name.lower()]
    filtered_count = original_count - len(audiobook_files)
    if filtered_count > 0:
        print(f"  Filtered out {filtered_count} cover art files")

    # Deduplicate: prefer main Library over /Library/Audiobook/
    main_library = [f for f in audiobook_files if "/Library/Audiobook/" not in str(f)]
    audiobook_folder = [f for f in audiobook_files if "/Library/Audiobook/" in str(f)]

    main_titles = {f.stem for f in main_library}
    unique_from_audiobook = [f for f in audiobook_folder if f.stem not in main_titles]

    result = main_library + unique_from_audiobook

    if len(audiobook_folder) > len(unique_from_audiobook):
        dup_count = len(audiobook_folder) - len(unique_from_audiobook)
        print(
            f"  Deduplicated {dup_count} files from /Library/Audiobook/ "
            f"(keeping {len(unique_from_audiobook)} unique)"
        )

    return result


# =============================================================================
# Statistics and Output
# =============================================================================

def print_scan_statistics(audiobooks: list[dict]) -> None:
    """Print summary statistics for scanned audiobooks."""
    print("\n" + "=" * 60)
    print("SCAN COMPLETE")
    print("=" * 60)
    print(f"Total audiobooks: {len(audiobooks)}")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Cover images: {COVER_DIR}")

    authors = set(ab["author"] for ab in audiobooks)
    genres = set(ab["genre_subcategory"] for ab in audiobooks)
    publishers = set(ab["publisher"] for ab in audiobooks)

    print(f"\nUnique authors: {len(authors)}")
    print(f"Unique genres: {len(genres)}")
    print(f"Unique publishers: {len(publishers)}")

    total_hours = sum(ab["duration_hours"] for ab in audiobooks)
    print(
        f"\nTotal listening time: {int(total_hours)} hours ({int(total_hours / 24)} days)"
    )




# =============================================================================
# Main Scanner
# =============================================================================

def scan_audiobooks() -> None:
    """Main scanning function."""
    print(f"Scanning audiobooks in {AUDIOBOOK_DIR}...")
    print(f"Supported formats: {', '.join(SUPPORTED_FORMATS)}")
    print()

    # Create output directories
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)

    # Find all audiobook files
    audiobook_files = find_audiobook_files(AUDIOBOOK_DIR, SUPPORTED_FORMATS)
    total_files = len(audiobook_files)
    print(f"\nTotal audiobook files: {total_files}")
    print()

    audiobooks = []
    progress = ProgressTracker(total_files)

    for idx, filepath in enumerate(audiobook_files, 1):
        progress.update(idx, filepath.name)

        metadata = get_file_metadata(filepath)
        if not metadata:
            continue

        # Extract cover art
        cover_path = extract_cover_art(filepath, COVER_DIR)
        metadata["cover_path"] = cover_path

        # Enrich with derived fields (genre categories, era, topics)
        metadata = enrich_metadata(metadata)

        audiobooks.append(metadata)

    progress.finish()

    # Save to JSON
    print(f"\nSaving metadata to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now().isoformat(),
                "total_audiobooks": len(audiobooks),
                "audiobooks": audiobooks,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print_scan_statistics(audiobooks)


if __name__ == "__main__":
    scan_audiobooks()
