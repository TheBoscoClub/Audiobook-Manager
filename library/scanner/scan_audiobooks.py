#!/usr/bin/env python3
"""
Audiobook Metadata Scanner
Scans audiobook directory and extracts metadata from various audio formats
Supports: .m4b, .opus, .m4a, .mp3
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOK_DIR, COVER_DIR, DATA_DIR
from utils import calculate_sha256

# Configuration
OUTPUT_FILE = DATA_DIR / "audiobooks.json"
SUPPORTED_FORMATS = [".m4b", ".opus", ".m4a", ".mp3"]


def get_file_metadata(filepath, calculate_hash=True):
    """Extract metadata from audiobook file using ffprobe"""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(filepath),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error reading {filepath}: {result.stderr}", file=sys.stderr)
            return None

        data = json.loads(result.stdout)

        # Extract relevant metadata
        format_data = data.get("format", {})
        tags = format_data.get("tags", {})

        # Normalize tag keys (handle case variations)
        tags_normalized = {k.lower(): v for k, v in tags.items()}

        # Calculate duration
        duration_sec = float(format_data.get("duration", 0))
        duration_hours = duration_sec / 3600

        # Extract author from folder structure for all audio files
        # Path structure: .../Library/Author Name/Book Title/Book Title.opus
        author_from_path = None
        parts = filepath.parts

        if "Library" in parts:
            library_idx = parts.index("Library")
            if len(parts) > library_idx + 1:
                potential_author = parts[library_idx + 1]
                # Skip "Audiobook" folder - use next level if present
                if potential_author.lower() == "audiobook":
                    if len(parts) > library_idx + 2:
                        author_from_path = parts[library_idx + 2]
                else:
                    author_from_path = potential_author

        # Try multiple metadata fields for author (in priority order)
        author_fields = ["artist", "album_artist", "author", "writer", "creator"]
        author = None
        for field in author_fields:
            if field in tags_normalized and tags_normalized[field]:
                author = tags_normalized[field]
                break
        if not author:
            author = author_from_path or "Unknown Author"

        # Try multiple metadata fields for narrator (in priority order)
        # Audiobooks often store narrator in various fields
        narrator_fields = [
            "narrator",
            "composer",
            "performer",
            "read_by",
            "narrated_by",
            "reader",
        ]
        narrator = None
        for field in narrator_fields:
            if field in tags_normalized and tags_normalized[field]:
                val = tags_normalized[field]
                # Skip if it's the same as author (sometimes composer = author)
                if val.lower() != author.lower() if author else True:
                    narrator = val
                    break
        if not narrator:
            narrator = "Unknown Narrator"

        # Calculate SHA-256 hash if requested
        file_hash = None
        hash_verified_at = None
        if calculate_hash:
            file_hash = calculate_sha256(filepath)
            if file_hash:
                hash_verified_at = datetime.now().isoformat()

        # Extract metadata
        metadata = {
            "title": tags_normalized.get(
                "title", tags_normalized.get("album", filepath.stem)
            ),
            "author": author,
            "narrator": narrator,
            "publisher": tags_normalized.get(
                "publisher", tags_normalized.get("label", "Unknown Publisher")
            ),
            "genre": tags_normalized.get("genre", "Uncategorized"),
            "year": tags_normalized.get("date", tags_normalized.get("year", "")),
            "description": tags_normalized.get(
                "comment", tags_normalized.get("description", "")
            ),
            "duration_hours": round(duration_hours, 2),
            "duration_formatted": f"{int(duration_hours)}h {int((duration_hours % 1) * 60)}m",
            "file_size_mb": round(filepath.stat().st_size / (1024 * 1024), 2),
            "file_path": str(filepath),
            "relative_path": str(filepath.relative_to(AUDIOBOOK_DIR)),
            "series": tags_normalized.get("series", ""),
            "series_part": tags_normalized.get("series-part", ""),
            "sha256_hash": file_hash,
            "hash_verified_at": hash_verified_at,
        }

        return metadata

    except Exception as e:
        print(f"Error processing {filepath}: {e}", file=sys.stderr)
        return None


def extract_cover_art(filepath, output_dir):
    """Extract cover art from audiobook file"""
    try:
        # Generate unique filename based on file path
        file_hash = hashlib.md5(str(filepath).encode()).hexdigest()
        cover_path = output_dir / f"{file_hash}.jpg"

        # Skip if already extracted
        if cover_path.exists():
            return cover_path.name

        cmd = [
            "ffmpeg",
            "-v",
            "quiet",
            "-i",
            str(filepath),
            "-an",  # No audio
            "-vcodec",
            "copy",
            str(cover_path),
        ]

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0 and cover_path.exists():
            return cover_path.name
        else:
            return None

    except Exception as e:
        print(f"Error extracting cover from {filepath}: {e}", file=sys.stderr)
        return None


def categorize_genre(genre):
    """Categorize genre into main category, subcategory, and sub-subcategory"""
    genre_lower = genre.lower()

    # Genre taxonomy
    categories = {
        "fiction": {
            "mystery & thriller": [
                "mystery",
                "thriller",
                "crime",
                "detective",
                "noir",
                "suspense",
            ],
            "science fiction": [
                "science fiction",
                "sci-fi",
                "scifi",
                "cyberpunk",
                "space opera",
            ],
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


def determine_literary_era(year_str):
    """Determine literary era based on publication year"""
    try:
        year = int(year_str[:4]) if year_str else 0

        if year == 0:
            return "Unknown Era"
        elif year < 1800:
            return "Classical (Pre-1800)"
        elif 1800 <= year < 1900:
            return "19th Century (1800-1899)"
        elif 1900 <= year < 1950:
            return "Early 20th Century (1900-1949)"
        elif 1950 <= year < 2000:
            return "Late 20th Century (1950-1999)"
        elif 2000 <= year < 2010:
            return "21st Century - Early (2000-2009)"
        elif 2010 <= year < 2020:
            return "21st Century - Modern (2010-2019)"
        else:
            return "21st Century - Contemporary (2020+)"

    except (ValueError, TypeError, AttributeError):
        return "Unknown Era"


def scan_audiobooks():
    """Main scanning function"""
    print(f"Scanning audiobooks in {AUDIOBOOK_DIR}...")
    print(f"Supported formats: {', '.join(SUPPORTED_FORMATS)}")
    print()

    # Create output directories
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)

    # Find all audiobook files (multiple formats)
    audiobook_files = []
    for ext in SUPPORTED_FORMATS:
        files = list(AUDIOBOOK_DIR.rglob(f"*{ext}"))
        print(f"  Found {len(files)} {ext} files")
        audiobook_files.extend(files)

    # Filter out cover art files (*.cover.opus, *.cover.m4b, etc.)
    original_count = len(audiobook_files)
    audiobook_files = [f for f in audiobook_files if ".cover." not in f.name.lower()]
    filtered_count = original_count - len(audiobook_files)
    if filtered_count > 0:
        print(f"  Filtered out {filtered_count} cover art files")

    # Deduplicate: prefer files from main Library over /Library/Audiobook/ (which may have duplicates)
    main_library_files = [
        f for f in audiobook_files if "/Library/Audiobook/" not in str(f)
    ]
    audiobook_folder_files = [
        f for f in audiobook_files if "/Library/Audiobook/" in str(f)
    ]

    # Get titles from main library
    main_titles = {f.stem for f in main_library_files}

    # Add Audiobook/ files only if title doesn't exist in main library
    unique_audiobook_files = [
        f for f in audiobook_folder_files if f.stem not in main_titles
    ]

    # Combine: main library + unique from Audiobook/
    audiobook_files = main_library_files + unique_audiobook_files

    if len(audiobook_folder_files) > len(unique_audiobook_files):
        dup_count = len(audiobook_folder_files) - len(unique_audiobook_files)
        print(
            f"  Deduplicated {dup_count} files from /Library/Audiobook/ (keeping {len(unique_audiobook_files)} unique)"
        )

    print(f"\nTotal audiobook files: {len(audiobook_files)}")
    print()

    audiobooks = []

    total_files = len(audiobook_files)
    for idx, filepath in enumerate(audiobook_files, 1):
        pct = int(idx * 100 / total_files)
        print(f"[{pct:3d}%] Processing {idx}/{total_files}: {filepath.name}")

        metadata = get_file_metadata(filepath)
        if not metadata:
            continue

        # Extract cover art
        cover_path = extract_cover_art(filepath, COVER_DIR)
        if cover_path:
            metadata["cover_path"] = str(cover_path)
        else:
            metadata["cover_path"] = None

        # Add file format
        metadata["format"] = filepath.suffix.lower().replace(".", "")

        # Add categorization
        genre_cat = categorize_genre(metadata["genre"])
        metadata["genre_category"] = genre_cat["main"]
        metadata["genre_subcategory"] = genre_cat["sub"]
        metadata["genre_original"] = genre_cat["original"]

        # Add literary era
        metadata["literary_era"] = determine_literary_era(metadata["year"])

        # Extract topics from description (simple keyword extraction)
        description_lower = metadata["description"].lower()
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

        metadata["topics"] = topics if topics else ["general"]

        audiobooks.append(metadata)

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

    # Generate statistics
    print("\n" + "=" * 60)
    print("SCAN COMPLETE")
    print("=" * 60)
    print(f"Total audiobooks: {len(audiobooks)}")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Cover images: {COVER_DIR}")

    # Show some statistics
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


if __name__ == "__main__":
    scan_audiobooks()
