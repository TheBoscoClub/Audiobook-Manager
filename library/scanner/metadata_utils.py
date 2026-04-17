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


def _extract_asin_from_chapters_json(filepath: Path) -> Optional[str]:
    """Source 1: Extract ASIN from chapters.json alongside the audiobook."""
    chapters_path = filepath.parent / "chapters.json"
    if not chapters_path.exists():
        return None
    try:
        with open(chapters_path, "r") as f:
            chapters_data = json.load(f)
        content_metadata = chapters_data.get("content_metadata", {})
        content_reference = content_metadata.get("content_reference", {})
        return content_reference.get("asin")
    except (json.JSONDecodeError, IOError):
        return None


def _normalize_title_for_matching(title: str) -> str:
    """Strip punctuation and lowercase for fuzzy title matching."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _extract_asin_from_voucher(filepath: Path, sources_dir: Path) -> Optional[str]:
    """Source 2: Extract ASIN from .voucher files in Sources directory.

    Matches voucher to library book by checking if the book's title appears
    in the voucher filename (normalized comparison).
    """
    book_title = _normalize_title_for_matching(filepath.stem)
    if not book_title:
        return None

    try:
        voucher_files = list(sources_dir.glob("*.voucher"))
    except OSError:
        return None

    for voucher_path in voucher_files:
        voucher_title = _normalize_title_for_matching(
            voucher_path.stem.split("_", 1)[-1].rsplit("-", 1)[0].replace("_", " ")
        )
        if not voucher_title or book_title not in voucher_title:
            continue
        try:
            with open(voucher_path, "r") as f:
                voucher_data = json.load(f)
            asin = voucher_data.get("content_license", {}).get("asin")
            if not asin:
                asin = (
                    voucher_data.get("content_license", {})
                    .get("content_metadata", {})
                    .get("content_reference", {})
                    .get("asin")
                )
            if asin:
                return asin
        except (json.JSONDecodeError, IOError):
            continue
    return None


_ASIN_FILENAME_RE = re.compile(r"^([B0-9][A-Z0-9]{9})_(.+)-AAX", re.IGNORECASE)


def _extract_asin_from_filename(filepath: Path, sources_dir: Path) -> Optional[str]:
    """Source 3: Extract ASIN from source filename pattern {ASIN}_Title-*.aaxc."""
    book_title = _normalize_title_for_matching(filepath.stem)
    if not book_title:
        return None

    try:
        source_files = list(sources_dir.glob("*.aaxc"))
    except OSError:
        return None

    for source_path in source_files:
        m = _ASIN_FILENAME_RE.match(source_path.name)
        if not m:
            continue
        candidate_asin = m.group(1)
        source_title = _normalize_title_for_matching(m.group(2).replace("_", " "))
        if book_title in source_title or source_title in book_title:
            return candidate_asin
    return None


def extract_asin(filepath: Path, sources_dir: Optional[Path] = None) -> Optional[str]:
    """Extract ASIN from any available source, checked in priority order.

    1. chapters.json (same directory as audiobook)
    2. .voucher file in Sources directory (if sources_dir provided)
    3. Source filename pattern in Sources directory (if sources_dir provided)
    """
    # Source 1: chapters.json (always available)
    asin = _extract_asin_from_chapters_json(filepath)
    if asin:
        return asin

    # Sources 2 & 3 require sources_dir
    if sources_dir and sources_dir.is_dir():
        asin = _extract_asin_from_voucher(filepath, sources_dir)
        if asin:
            return asin

        asin = _extract_asin_from_filename(filepath, sources_dir)
        if asin:
            return asin

    return None


def _merge_tags(data: dict) -> dict:
    """Extract and merge format-level and stream-level tags.

    Opus/Ogg stores metadata in streams[0].tags, not format.tags.
    Stream tags take precedence when format tags are empty.
    """
    format_data = data.get("format", {})
    tags = format_data.get("tags", {})
    streams = data.get("streams", [])
    stream_tags = streams[0].get("tags", {}) if streams else {}

    if not tags:
        return stream_tags

    # Merge stream tags for fields missing from format tags
    merged = dict(tags)
    for k, v in stream_tags.items():
        if k not in merged:
            merged[k] = v
    return merged


def _parse_publication_date(raw_date: str) -> tuple[int | None, str | None]:
    """Parse year and full date from a raw date string.

    Returns (published_year, published_date).
    """
    if not raw_date:
        return None, None

    published_year = None
    published_date = None
    year_match = re.search(r"\d{4}", str(raw_date))
    if year_match:
        published_year = int(year_match.group())
    full_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(raw_date))
    if full_date_match:
        published_date = full_date_match.group(1)
    return published_year, published_date


def _compute_hash(
    filepath: Path, calculate_hash: bool
) -> tuple[str | None, str | None]:
    """Calculate SHA-256 hash if requested. Returns (hash, verified_at)."""
    if not calculate_hash:
        return None, None
    file_hash = calculate_sha256(filepath)
    verified_at = datetime.now().isoformat() if file_hash else None
    return file_hash, verified_at


def _build_metadata_dict(
    filepath: Path,
    tags_normalized: dict,
    format_data: dict,
    audiobook_dir: Path,
    calculate_hash: bool,
) -> dict:
    """Build the final metadata dictionary from normalized tags and format data."""
    duration_sec = float(format_data.get("duration", 0))
    duration_hours = duration_sec / 3600

    author_from_path = extract_author_from_path(filepath)
    author = extract_author_from_tags(tags_normalized, author_from_path)
    narrator = extract_narrator_from_tags(tags_normalized, author)

    file_hash, hash_verified_at = _compute_hash(filepath, calculate_hash)
    asin = extract_asin(filepath)
    raw_date = tags_normalized.get("date", tags_normalized.get("year", ""))
    published_year, published_date = _parse_publication_date(raw_date)

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
        "acquired_date": datetime.now().strftime("%Y-%m-%d"),
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

    try:
        metadata["relative_path"] = str(filepath.relative_to(audiobook_dir))
    except ValueError:
        metadata["relative_path"] = str(filepath)

    return metadata


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

        tags = _merge_tags(data)
        tags_normalized = {k.lower(): v for k, v in tags.items()}
        format_data = data.get("format", {})

        return _build_metadata_dict(
            filepath, tags_normalized, format_data, audiobook_dir, calculate_hash
        )

    except Exception as e:
        print(f"Error processing {filepath}: {e}", file=sys.stderr)
        return None


def _cover_path_for_file(filepath: Path, output_dir: Path) -> Path:
    """Generate the deterministic cover art path for an audio file."""
    file_hash = hashlib.md5(str(filepath).encode(), usedforsecurity=False).hexdigest()
    return output_dir / f"{file_hash}.jpg"


def _extract_embedded_cover(
    filepath: Path, cover_path: Path, timeout: int
) -> str | None:
    """Try extracting embedded cover art via ffmpeg. Returns filename or None."""
    cmd = [
        "ffmpeg",
        "-v",
        "quiet",
        "-i",
        str(filepath),
        "-an",
        "-vcodec",
        "copy",
        str(cover_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode == 0 and cover_path.exists():
        return cover_path.name
    if result.returncode == 0 and not cover_path.exists():
        print(
            f"Warning: ffmpeg succeeded but cover not created at {cover_path} "
            f"— check filesystem permissions or systemd ReadWritePaths",
            file=sys.stderr,
        )
    return None


def _copy_standalone_cover(filepath: Path, cover_path: Path) -> str | None:
    """Try copying a sidecar cover file. Returns filename or None."""
    standalone = _find_standalone_cover(filepath)
    if not standalone:
        return None
    try:
        shutil.copy2(standalone, cover_path)
        return cover_path.name
    except OSError as copy_err:
        print(
            f"Warning: found cover {standalone} but copy failed: {copy_err}",
            file=sys.stderr,
        )
        return None


def _resolve_external_cover(
    metadata: Optional[dict], output_dir: Path, timeout: int
) -> str | None:
    """Try external API resolver (Audible/OpenLibrary/Google Books)."""
    if not metadata or not metadata.get("title"):
        return None
    try:
        # Scanner runs with `library/` on sys.path; canonical path is
        # `scanner.utils.cover_resolver`. The legacy `utils.cover_resolver`
        # never resolved (silently disabled the fallback).
        from scanner.utils.cover_resolver import resolve_cover

        return resolve_cover(
            title=metadata["title"],
            author=metadata.get("author"),
            asin=metadata.get("asin"),
            output_dir=output_dir,
            timeout=timeout,
        )
    except ImportError:
        return None
    except Exception as e:
        print(f"Warning: external cover resolver failed: {e}", file=sys.stderr)
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

    Returns the cover filename if successful, None otherwise.
    """
    try:
        cover_path = _cover_path_for_file(filepath, output_dir)

        if cover_path.exists():
            return cover_path.name

        # Tier 1: embedded cover via ffmpeg
        result = _extract_embedded_cover(filepath, cover_path, timeout)
        if result:
            return result

        # Tier 2: standalone sidecar file
        result = _copy_standalone_cover(filepath, cover_path)
        if result:
            return result

        # Tier 3: external API resolver
        return _resolve_external_cover(metadata, output_dir, timeout)

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
