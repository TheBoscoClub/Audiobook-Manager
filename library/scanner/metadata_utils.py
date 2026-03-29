"""
Shared metadata extraction utilities for audiobook scanning.

This module provides common functions used by both full scanners and
incremental adders to extract and categorize audiobook metadata.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from common import calculate_sha256

# =============================================================================
# Genre and Topic Classification
# =============================================================================

# Content types are NOT genres — filter these out of genre classification.
# "Audiobook" describes the format/medium, not the literary genre.
CONTENT_TYPES = {
    "audiobook",
    "podcast",
    "lecture",
    "speech",
    "performance",
    "radio",
    "radio drama",
    "audio drama",
    "full cast",
    "unabridged",
    "abridged",
    "original recording",
}

# Genre taxonomy for categorization.
# Keys are internal category names; values map subcategories to match keywords.
GENRE_TAXONOMY = {
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

# Map internal taxonomy subcategory names → display names used by collections UI.
# These must match the genre names queried in collections.py exactly.
GENRE_DISPLAY_NAMES = {
    "mystery & thriller": "Mystery",
    "science fiction": "Science Fiction",
    "fantasy": "Fantasy",
    "literary fiction": "Literary Fiction",
    "horror": "Horror",
    "romance": "Romance",
    "biography & memoir": "Biographies & Memoirs",
    "history": "History",
    "science": "Science",
    "philosophy": "Philosophy",
    "self-help": "Personal Development",
    "business": "Business & Careers",
    "true crime": "True Crime",
}

# Topic keywords for extraction
TOPIC_KEYWORDS = {
    "war": ["war", "battle", "military", "conflict"],
    "adventure": ["adventure", "journey", "quest", "expedition"],
    "technology": ["technology", "computer", "ai", "artificial intelligence"],
    "politics": ["politics", "political", "government", "election"],
    "religion": ["religion", "faith", "spiritual", "god"],
    "family": ["family", "parent", "child", "marriage"],
    "society": ["society", "social", "culture", "community"],
}


def is_content_type(genre: str) -> bool:
    """Return True if the value describes a content type, not a literary genre."""
    return genre.lower().strip() in CONTENT_TYPES


def categorize_genre(genre: str | None) -> dict:
    """Categorize genre into main category, subcategory, and original.

    Content types like "Audiobook" are classified as uncategorized since
    they describe the medium, not the literary genre.

    Matches longer keywords first to avoid partial-match ambiguity
    (e.g., "true crime" should not match "crime" → mystery).
    """
    if not genre:
        return {"main": "uncategorized", "sub": "general", "original": ""}

    if is_content_type(genre):
        return {"main": "uncategorized", "sub": "general", "original": genre}

    genre_lower = genre.lower()

    # Build flat list of (keyword, main_cat, subcat), sorted longest-first
    # so "true crime" matches before "crime", "historical fiction" before "historical"
    candidates = []
    for main_cat, subcats in GENRE_TAXONOMY.items():
        for subcat, keywords in subcats.items():
            for kw in keywords:
                candidates.append((kw, main_cat, subcat))
    candidates.sort(key=lambda x: -len(x[0]))

    for kw, main_cat, subcat in candidates:
        if kw in genre_lower:
            return {"main": main_cat, "sub": subcat, "original": genre}

    return {"main": "uncategorized", "sub": "general", "original": genre}


def determine_literary_era(year_str: str) -> str:
    """Determine literary era based on publication year."""
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

    except (ValueError, TypeError, AttributeError):
        return "Unknown Era"


def extract_topics(description: str) -> list[str]:
    """Extract topics from description using keyword matching."""
    description_lower = description.lower()
    topics = []

    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in description_lower for kw in keywords):
            topics.append(topic)

    return topics if topics else ["general"]


# =============================================================================
# Metadata Extraction Helpers
# =============================================================================


def extract_author_from_path(filepath: Path) -> str | None:
    """
    Extract author name from file path structure.

    Expected structure: .../Library/Author Name/Book Title/file.opus
    """
    parts = filepath.parts

    if "Library" not in parts:
        return None

    library_idx = parts.index("Library")
    if len(parts) <= library_idx + 1:
        return None

    potential_author = parts[library_idx + 1]

    # Skip "Audiobook" folder - use next level if present
    if potential_author.lower() == "audiobook":
        if len(parts) > library_idx + 2:
            return parts[library_idx + 2]
        return None

    return potential_author


def extract_author_from_tags(tags: dict, fallback: str | None = None) -> str:
    """
    Extract author from metadata tags.

    Tries multiple common tag fields in priority order.
    """
    author_fields = ["artist", "album_artist", "author", "writer", "creator"]

    for field in author_fields:
        if field in tags and tags[field]:
            return tags[field]

    return fallback or "Unknown Author"


def extract_narrator_from_tags(tags: dict, author: str | None = None) -> str:
    """
    Extract narrator from metadata tags.

    Tries multiple common tag fields, avoiding author if same value.
    """
    narrator_fields = [
        "narrator",
        "composer",
        "performer",
        "read_by",
        "narrated_by",
        "reader",
    ]

    for field in narrator_fields:
        if field in tags and tags[field]:
            val = tags[field]
            # Skip if it's the same as author
            if author and val.lower() == author.lower():
                continue
            return val

    return "Unknown Narrator"


def run_ffprobe(filepath: Path, timeout: int = 30) -> dict | None:
    """
    Run ffprobe on a file and return parsed JSON data.

    Returns None if ffprobe fails or times out.
    """
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

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"Error reading {filepath}: {result.stderr}", file=sys.stderr)
            return None

        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print(f"Timeout reading {filepath}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Invalid JSON from ffprobe for {filepath}: {e}", file=sys.stderr)
        return None


def extract_asin_from_chapters_json(filepath: Path) -> Optional[str]:
    """
    Extract ASIN from chapters.json in the same directory as the audiobook.

    AAXtoMP3 creates chapters.json alongside converted audiobooks containing
    the original Audible ASIN, used for deduplication and edition tracking.
    The ASIN is nested at: content_metadata.content_reference.asin
    """
    chapters_path = filepath.parent / "chapters.json"
    if not chapters_path.exists():
        return None

    try:
        with open(chapters_path, "r") as f:
            chapters_data = json.load(f)
        # ASIN is nested in content_metadata.content_reference
        content_metadata = chapters_data.get("content_metadata", {})
        content_reference = content_metadata.get("content_reference", {})
        return content_reference.get("asin")
    except (json.JSONDecodeError, IOError):
        return None


def get_file_metadata(
    filepath: Path, audiobook_dir: Path, calculate_hash: bool = True
) -> Optional[dict]:
    """
    Extract metadata from audiobook file using ffprobe.

    Args:
        filepath: Path to the audiobook file
        audiobook_dir: Base audiobook directory for relative path calculation
        calculate_hash: Whether to calculate SHA-256 hash

    Returns:
        Metadata dict or None if extraction failed
    """
    try:
        data = run_ffprobe(filepath)
        if not data:
            return None

        # Extract relevant metadata
        format_data = data.get("format", {})
        tags = format_data.get("tags", {})

        # Opus/Ogg stores metadata in streams[0].tags, not format.tags.
        # Check both locations — stream tags take precedence when format
        # tags are empty (common for all Opus files in this library).
        if not tags:
            streams = data.get("streams", [])
            if streams:
                tags = streams[0].get("tags", {})
        else:
            # Merge stream tags for fields missing from format tags
            streams = data.get("streams", [])
            if streams:
                stream_tags = streams[0].get("tags", {})
                for k, v in stream_tags.items():
                    if k not in tags:
                        tags[k] = v

        # Normalize tag keys (handle case variations)
        tags_normalized = {k.lower(): v for k, v in tags.items()}

        # Calculate duration
        duration_sec = float(format_data.get("duration", 0))
        duration_hours = duration_sec / 3600

        # Extract author
        author_from_path = extract_author_from_path(filepath)
        author = extract_author_from_tags(tags_normalized, author_from_path)

        # Extract narrator
        narrator = extract_narrator_from_tags(tags_normalized, author)

        # Calculate SHA-256 hash if requested
        file_hash = None
        hash_verified_at = None
        if calculate_hash:
            file_hash = calculate_sha256(filepath)
            if file_hash:
                hash_verified_at = datetime.now().isoformat()

        # Extract ASIN from chapters.json if present
        asin = extract_asin_from_chapters_json(filepath)

        # Parse publication date from tags (may be "2015", "2015-03-17", etc.)
        raw_date = tags_normalized.get("date", tags_normalized.get("year", ""))
        published_year = None
        published_date = None
        if raw_date:
            year_match = re.search(r"\d{4}", str(raw_date))
            if year_match:
                published_year = int(year_match.group())
            # Try to extract full date (YYYY-MM-DD)
            full_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(raw_date))
            if full_date_match:
                published_date = full_date_match.group(1)

        # acquired_date = now (the moment this book enters the library)
        acquired_date = datetime.now().strftime("%Y-%m-%d")

        # Build metadata dict
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
            "year": raw_date,
            "published_year": published_year,
            "published_date": published_date,
            "acquired_date": acquired_date,
            "description": tags_normalized.get(
                "comment", tags_normalized.get("description", "")
            ),
            "duration_hours": round(duration_hours, 2),
            "duration_formatted": (
                f"{int(duration_hours)}h {int((duration_hours % 1) * 60)}m"
            ),
            "file_size_mb": round(filepath.stat().st_size / (1024 * 1024), 2),
            "file_path": str(filepath),
            "series": tags_normalized.get("series", ""),
            "series_part": tags_normalized.get("series-part", ""),
            "sha256_hash": file_hash,
            "hash_verified_at": hash_verified_at,
            "format": filepath.suffix.lower().replace(".", ""),
            "asin": asin,
        }

        # Add relative path if audiobook_dir provided
        try:
            metadata["relative_path"] = str(filepath.relative_to(audiobook_dir))
        except ValueError:
            metadata["relative_path"] = str(filepath)

        return metadata

    except Exception as e:
        print(f"Error processing {filepath}: {e}", file=sys.stderr)
        return None


def extract_cover_art(
    filepath: Path,
    output_dir: Path,
    timeout: int = 30,
    metadata: Optional[dict] = None,
) -> str | None:
    """
    Extract cover art from audiobook file.

    Tiered strategy:
      1. ffmpeg — extract embedded cover art from the audio file
      2. Standalone sidecar — look for {title}.jpg or cover.jpg next to the file
      3. External resolver — query Audible/OpenLibrary/Google Books APIs
         (requires metadata dict with 'title' and optionally 'author', 'asin')

    Returns the cover filename if successful, None otherwise.
    """
    try:
        # Generate unique filename based on file path
        file_hash = hashlib.md5(
            str(filepath).encode(), usedforsecurity=False
        ).hexdigest()
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

        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0 and cover_path.exists():
            return cover_path.name
        if result.returncode == 0 and not cover_path.exists():
            # ffmpeg succeeded but file not created — likely a read-only filesystem
            # (e.g. ProtectSystem=strict without the data dir in ReadWritePaths)
            print(
                f"Warning: ffmpeg succeeded but cover not created at {cover_path} "
                f"— check filesystem permissions or systemd ReadWritePaths",
                file=sys.stderr,
            )

        # Fallback: look for standalone cover files in the audio file's directory.
        # AAXtoMP3 extracts covers as {title}.jpg alongside the opus file, but
        # can't embed them into Opus containers without mutagen. Rather than lose
        # the cover, copy the standalone file into the centralized covers dir.
        standalone = _find_standalone_cover(filepath)
        if standalone:
            try:
                shutil.copy2(standalone, cover_path)
                return cover_path.name
            except OSError as copy_err:
                print(
                    f"Warning: found cover {standalone} but copy failed: {copy_err}",
                    file=sys.stderr,
                )

        # Tier 3: External API resolver (Audible → Open Library → Google Books)
        if metadata and metadata.get("title"):
            try:
                from utils.cover_resolver import resolve_cover

                resolved = resolve_cover(
                    title=metadata["title"],
                    author=metadata.get("author"),
                    asin=metadata.get("asin"),
                    output_dir=output_dir,
                    timeout=timeout,
                )
                if resolved:
                    return resolved
            except ImportError:
                pass
            except Exception as e:
                print(
                    f"Warning: external cover resolver failed: {e}",
                    file=sys.stderr,
                )

        return None

    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"Error extracting cover from {filepath}: {e}", file=sys.stderr)
        return None


def _find_standalone_cover(filepath: Path) -> Path | None:
    """Find a standalone cover image file next to the audio file.

    Search order: {stem}.jpg, {stem}.png, cover.jpg, cover.png
    """
    parent = filepath.parent
    stem = filepath.stem
    for candidate in (
        parent / f"{stem}.jpg",
        parent / f"{stem}.png",
        parent / "cover.jpg",
        parent / "cover.png",
    ):
        if candidate.is_file():
            return candidate
    return None


def build_genres_list(genre_cat: dict) -> list[str]:
    """Build a genres list from categorized genre data.

    Returns display-name genres suitable for the genres/audiobook_genres tables.
    Returns empty list if the genre is uncategorized (e.g., content type "Audiobook").
    """
    if genre_cat["main"] == "uncategorized":
        return []

    subcat = genre_cat["sub"]
    display_name = GENRE_DISPLAY_NAMES.get(subcat)
    if display_name:
        return [display_name]

    # Subcategory not in display map — use title-cased subcategory as fallback
    return [subcat.title()]


def enrich_metadata(metadata: dict) -> dict:
    """
    Add derived fields to metadata (genre categories, era, topics).

    This enriches the raw metadata with computed categorizations.
    """
    # Add genre categorization
    genre_cat = categorize_genre(metadata.get("genre", ""))
    metadata["genre_category"] = genre_cat["main"]
    metadata["genre_subcategory"] = genre_cat["sub"]
    metadata["genre_original"] = genre_cat["original"]

    # Build genres list for the importer (populates genres/audiobook_genres tables)
    metadata["genres"] = build_genres_list(genre_cat)

    # Add literary era
    era = determine_literary_era(metadata.get("year", ""))
    metadata["literary_era"] = era
    metadata["eras"] = [era] if era else []

    # Extract topics
    metadata["topics"] = extract_topics(metadata.get("description", ""))

    return metadata
