#!/usr/bin/env python3
"""
================================================================================
WARNING: EXPERIMENTAL / NOT FULLY TESTED - USE AT YOUR OWN RISK
================================================================================
This script processes non-AAXC audiobook formats (ZIP, MP3, M4A, M4B) into OPUS.
These formats are NOT fully tested and may not work as expected.

KNOWN ISSUES:
- Metadata extraction may be incomplete or incorrect
- Chapter detection/ordering may fail for some sources
- Cover art extraction is unreliable for many formats
- Multi-reader audiobooks (e.g., Librivox) may not be handled correctly

The ONLY fully tested and verified format is Audible's AAXC format, which is
handled by the main audiobook conversion pipeline (convert-audiobooks-opus-parallel,
download-new-audiobooks, etc.)

This script is part of the multi-source audiobook support feature which has been
moved to "Phase Maybe" in the roadmap. The code exists and may work, but it is
not actively supported or prioritized.

If you want to use or finish this feature, you're welcome to - PRs accepted.
================================================================================

Process Google Play audiobook downloads into library-ready OPUS files.

Merges chapter MP3s into a single OPUS file with embedded metadata and cover art.
Follows existing library patterns: dry-run by default, --execute to apply.

Usage:
    # Dry run (preview)
    python3 google_play_processor.py /path/to/audiobook.zip

    # Execute
    python3 google_play_processor.py /path/to/audiobook.zip --execute

    # With database import
    python3 google_play_processor.py /path/to/audiobook.zip --import-db --execute

    # Skip OpenLibrary enrichment
    python3 google_play_processor.py /path/to/audiobook.zip --no-enrich --execute

    # Process directory of chapters
    python3 google_play_processor.py /path/to/chapters/ --execute
"""

import base64
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOKS_COVERS, AUDIOBOOKS_LIBRARY, DATABASE_PATH  # noqa: E402


def _set_low_priority():
    """Set low CPU and I/O priority for child processes."""
    os.nice(19)  # Lowest CPU priority

# Try to import mutagen for metadata handling
try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import APIC
    from mutagen.oggopus import OggOpus
    from mutagen.flac import Picture

    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False
    print("Warning: mutagen not installed. Cover art embedding will be disabled.")

# Try to import OpenLibrary client
try:
    from utils.openlibrary_client import OpenLibraryClient

    HAS_OPENLIBRARY = True
except ImportError:
    HAS_OPENLIBRARY = False

DB_PATH = DATABASE_PATH
LIBRARY_PATH = AUDIOBOOKS_LIBRARY
COVERS_PATH = AUDIOBOOKS_COVERS


class GooglePlayProcessor:
    """Process Google Play audiobook downloads into OPUS files."""

    def __init__(
        self,
        output_dir: Path = LIBRARY_PATH,
        covers_dir: Path = COVERS_PATH,
        enrich_metadata: bool = True,
        dry_run: bool = True,
        verbose: bool = False,
    ):
        """
        Initialize the processor.

        Args:
            output_dir: Target directory for processed audiobooks
            covers_dir: Directory for cover art storage
            enrich_metadata: Whether to query OpenLibrary for additional metadata
            dry_run: If True, only preview actions without making changes
            verbose: Show verbose output
        """
        self.output_dir = Path(output_dir)
        self.covers_dir = Path(covers_dir)
        self.enrich_metadata = enrich_metadata and HAS_OPENLIBRARY
        self.dry_run = dry_run
        self.verbose = verbose
        self.temp_dir = None

        if self.enrich_metadata:
            self.ol_client = OpenLibraryClient()
        else:
            self.ol_client = None

    def process(self, input_path: Path) -> Dict:
        """
        Process a Google Play download (ZIP or directory).

        Args:
            input_path: Path to ZIP file or directory containing chapter MP3s

        Returns:
            Dictionary with processing results
        """
        input_path = Path(input_path)
        result = {
            "success": False,
            "input": str(input_path),
            "chapters": 0,
            "duration": 0,
            "metadata": {},
            "output_path": None,
            "error": None,
        }

        try:
            # Determine input type
            if input_path.suffix.lower() == ".zip":
                work_dir = self._extract_zip(input_path)
                result["extracted"] = True
            elif input_path.is_dir():
                work_dir = input_path
                result["extracted"] = False
            else:
                result["error"] = f"Input must be a ZIP file or directory: {input_path}"
                return result

            # Find chapter files
            chapter_files = self._find_chapter_files(work_dir)
            if not chapter_files:
                result["error"] = f"No MP3 files found in: {work_dir}"
                return result

            result["chapters"] = len(chapter_files)

            # Extract metadata from chapters
            metadata = self._extract_metadata_from_chapters(chapter_files)
            result["metadata"] = metadata
            result["duration"] = metadata.get("duration_hours", 0)

            # Extract cover art
            cover_data, cover_mime = self._extract_cover_art(chapter_files)
            if cover_data:
                metadata["has_cover"] = True

            # Enrich with OpenLibrary if enabled
            if self.enrich_metadata and self.ol_client:
                enriched = self._enrich_from_openlibrary(metadata)
                metadata.update(enriched)

            # Create output structure
            output_dir = self._create_output_structure(metadata)
            output_file = (
                output_dir
                / f"{self._sanitize_filename(metadata.get('title', 'audiobook'))}.opus"
            )
            result["output_path"] = str(output_file)

            # Report what would happen
            self._print_summary(result, metadata, output_file)

            if self.dry_run:
                return result

            # Actual processing
            # Create output directory
            output_dir.mkdir(parents=True, exist_ok=True)

            # Merge chapters to OPUS
            if not self._merge_to_opus(chapter_files, output_file, metadata):
                result["error"] = "Failed to merge chapters to OPUS"
                return result

            # Embed cover art
            if cover_data and HAS_MUTAGEN:
                self._embed_opus_cover(output_file, cover_data, cover_mime)

                # Also save cover separately
                cover_ext = "jpg" if "jpeg" in cover_mime else "png"
                cover_hash = hashlib.md5(str(output_file).encode()).hexdigest()
                cover_path = self.covers_dir / f"{cover_hash}.{cover_ext}"
                if not cover_path.exists():
                    self.covers_dir.mkdir(parents=True, exist_ok=True)
                    with open(cover_path, "wb") as f:
                        f.write(cover_data)
                metadata["cover_path"] = str(cover_path)

            result["success"] = True
            print(f"\nOutput: {output_file}")

        except Exception as e:
            result["error"] = str(e)
            if self.verbose:
                import traceback

                traceback.print_exc()

        finally:
            # Clean up temp directory if we created one
            if self.temp_dir and self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)

        return result

    def _extract_zip(self, zip_path: Path) -> Path:
        """Extract ZIP file to temporary directory."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="gplay_"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(self.temp_dir)
        return self.temp_dir

    def _find_chapter_files(self, directory: Path) -> List[Path]:
        """Find and sort chapter audio files in directory (MP3, M4A, M4B)."""
        audio_files = []

        # Search recursively for common audiobook formats
        for pattern in [
            "*.mp3",
            "*.MP3",
            "*.m4a",
            "*.M4A",
            "*.m4b",
            "*.M4B",
            "*.aac",
            "*.AAC",
        ]:
            audio_files.extend(directory.rglob(pattern))

        if not audio_files:
            return []

        # Sort by filename (chapters are usually numbered)
        def sort_key(p):
            # Extract numbers from filename for natural sorting
            numbers = re.findall(r"\d+", p.stem)
            if numbers:
                return (int(numbers[0]), p.stem)
            return (999, p.stem)

        audio_files.sort(key=sort_key)
        return audio_files

    def _extract_metadata_from_chapters(self, chapter_files: List[Path]) -> Dict:
        """Extract metadata from chapter files using mutagen."""
        metadata = {
            "title": None,
            "author": None,
            "narrator": None,
            "album": None,
            "genre": None,
            "year": None,
            "duration_seconds": 0,
            "duration_hours": 0,
            "chapter_count": len(chapter_files),
            "source": "google_play",
        }

        if not HAS_MUTAGEN:
            # Fallback: try to parse from directory/filename
            parent = chapter_files[0].parent
            metadata["title"] = parent.name
            return metadata

        # Import additional mutagen formats
        from mutagen import File as MutagenFile

        total_duration = 0

        for i, chapter_file in enumerate(chapter_files):
            try:
                # Use generic File to handle MP3, M4A, etc.
                audio = MutagenFile(chapter_file)
                if audio is None:
                    continue

                total_duration += audio.info.length

                # Get metadata from first chapter only
                if i == 0:
                    suffix = chapter_file.suffix.lower()

                    if suffix == ".mp3" and audio.tags:
                        # MP3 with ID3 tags
                        tags = audio.tags
                        if "TALB" in tags:
                            metadata["album"] = str(tags["TALB"])
                            metadata["title"] = metadata["album"]
                        elif "TIT2" in tags:
                            title = str(tags["TIT2"])
                            title = re.sub(
                                r",?\s*(Chapter|Part|Section)\s*\d+.*$",
                                "",
                                title,
                                flags=re.IGNORECASE,
                            )
                            metadata["title"] = title
                        if "TPE1" in tags:
                            metadata["author"] = str(tags["TPE1"])
                        elif "TPE2" in tags:
                            metadata["author"] = str(tags["TPE2"])
                        if "TCOM" in tags:
                            metadata["narrator"] = str(tags["TCOM"])
                        if "TCON" in tags:
                            metadata["genre"] = str(tags["TCON"])
                        if "TDRC" in tags:
                            try:
                                metadata["year"] = int(str(tags["TDRC"])[:4])
                            except (ValueError, TypeError):
                                pass

                    elif suffix in [".m4a", ".m4b", ".aac"]:
                        # M4A/M4B/AAC with MP4 tags
                        tags = audio.tags or {}

                        # Album/Title
                        if "\xa9alb" in tags:
                            metadata["album"] = str(tags["\xa9alb"][0])
                            metadata["title"] = metadata["album"]
                        elif "\xa9nam" in tags:
                            title = str(tags["\xa9nam"][0])
                            title = re.sub(
                                r",?\s*(Chapter|Part|Section)\s*\d+.*$",
                                "",
                                title,
                                flags=re.IGNORECASE,
                            )
                            metadata["title"] = title

                        # Artist/Author
                        if "\xa9ART" in tags:
                            metadata["author"] = str(tags["\xa9ART"][0])
                        elif "aART" in tags:
                            metadata["author"] = str(tags["aART"][0])

                        # Narrator (composer or narrator tag)
                        if "\xa9wrt" in tags:
                            metadata["narrator"] = str(tags["\xa9wrt"][0])
                        elif "----:com.apple.iTunes:NARRATOR" in tags:
                            metadata["narrator"] = str(
                                tags["----:com.apple.iTunes:NARRATOR"][0]
                            )

                        # Genre
                        if "\xa9gen" in tags:
                            metadata["genre"] = str(tags["\xa9gen"][0])

                        # Year
                        if "\xa9day" in tags:
                            try:
                                year_str = str(tags["\xa9day"][0])
                                metadata["year"] = int(year_str[:4])
                            except (ValueError, TypeError, IndexError):
                                pass

            except Exception as e:
                if self.verbose:
                    print(f"Warning: Could not read metadata from {chapter_file}: {e}")

        metadata["duration_seconds"] = total_duration
        metadata["duration_hours"] = round(total_duration / 3600, 2)

        # Fallback title from directory name
        if not metadata["title"]:
            metadata["title"] = chapter_files[0].parent.name

        return metadata

    def _extract_cover_art(
        self, chapter_files: List[Path]
    ) -> Tuple[Optional[bytes], str]:
        """Extract embedded cover art from chapter files."""
        if not HAS_MUTAGEN:
            return None, ""

        from mutagen import File as MutagenFile

        for chapter_file in chapter_files[:3]:  # Check first 3 chapters
            try:
                suffix = chapter_file.suffix.lower()

                if suffix == ".mp3":
                    audio = MP3(chapter_file)
                    if audio.tags:
                        for tag in audio.tags.values():
                            if isinstance(tag, APIC):
                                return tag.data, tag.mime

                elif suffix in [".m4a", ".m4b", ".aac"]:
                    audio = MutagenFile(chapter_file)
                    if audio and audio.tags:
                        # M4A cover art is in 'covr' tag
                        if "covr" in audio.tags:
                            cover = audio.tags["covr"][0]
                            # Determine MIME type from format
                            if hasattr(cover, "imageformat"):
                                if cover.imageformat == 13:  # JPEG
                                    return bytes(cover), "image/jpeg"
                                elif cover.imageformat == 14:  # PNG
                                    return bytes(cover), "image/png"
                            return bytes(cover), "image/jpeg"  # Default to JPEG

            except Exception as e:
                if self.verbose:
                    print(f"Warning: Could not extract cover from {chapter_file}: {e}")
                continue

        # Also check for cover.jpg in directory
        cover_files = ["cover.jpg", "cover.jpeg", "cover.png", "folder.jpg"]
        directory = chapter_files[0].parent
        for cover_name in cover_files:
            cover_path = directory / cover_name
            if cover_path.exists():
                mime = (
                    "image/jpeg"
                    if cover_name.endswith((".jpg", ".jpeg"))
                    else "image/png"
                )
                with open(cover_path, "rb") as f:
                    return f.read(), mime

        return None, ""

    def _merge_to_opus(
        self, chapter_files: List[Path], output_path: Path, metadata: Dict
    ) -> bool:
        """Merge chapter files into single OPUS file using FFmpeg."""
        # Create concat file list
        concat_file = Path(tempfile.mktemp(suffix=".txt"))

        try:
            with open(concat_file, "w") as f:
                for chapter in chapter_files:
                    # Escape special characters
                    escaped = str(chapter).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            # Build FFmpeg command
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:a",
                "libopus",
                "-b:a",
                "64k",
                "-vbr",
                "on",
                "-compression_level",
                "10",
                "-application",
                "voip",
                "-map_metadata",
                "-1",  # Clear existing metadata
            ]

            # Add metadata
            if metadata.get("title"):
                cmd.extend(["-metadata", f"title={metadata['title']}"])
                cmd.extend(["-metadata", f"album={metadata['title']}"])
            if metadata.get("author"):
                cmd.extend(["-metadata", f"artist={metadata['author']}"])
                cmd.extend(["-metadata", f"album_artist={metadata['author']}"])
            if metadata.get("narrator"):
                cmd.extend(["-metadata", f"composer={metadata['narrator']}"])
            if metadata.get("genre"):
                cmd.extend(["-metadata", f"genre={metadata['genre']}"])
            if metadata.get("year"):
                cmd.extend(["-metadata", f"date={metadata['year']}"])

            cmd.append(str(output_path))

            if self.verbose:
                print(f"Running: {' '.join(cmd[:10])}...")

            result = subprocess.run(
                cmd, capture_output=True, text=True, preexec_fn=_set_low_priority
            )

            if result.returncode != 0:
                print(f"FFmpeg error: {result.stderr}")
                return False

            return True

        finally:
            if concat_file.exists():
                concat_file.unlink()

    def _embed_opus_cover(
        self, opus_path: Path, cover_data: bytes, mime_type: str = "image/jpeg"
    ) -> bool:
        """Embed cover art into OPUS file using mutagen."""
        if not HAS_MUTAGEN:
            return False

        try:
            audio = OggOpus(str(opus_path))

            picture = Picture()
            picture.type = 3  # Front cover
            picture.mime = mime_type
            picture.desc = "Front cover"
            picture.data = cover_data

            # Get image dimensions (optional, but helpful)
            try:
                from PIL import Image
                import io

                img = Image.open(io.BytesIO(cover_data))
                picture.width, picture.height = img.size
                picture.depth = 24  # Assume 24-bit color
            except ImportError:
                pass

            picture_data = picture.write()
            encoded_data = base64.b64encode(picture_data).decode("ascii")
            audio["metadata_block_picture"] = [encoded_data]
            audio.save()

            return True

        except Exception as e:
            print(f"Error embedding cover: {e}")
            return False

    def _create_output_structure(self, metadata: Dict) -> Path:
        """Create library output directory structure."""
        author = self._sanitize_filename(metadata.get("author", "Unknown Author"))
        title = self._sanitize_filename(metadata.get("title", "Unknown Title"))

        output_dir = self.output_dir / author / title
        return output_dir

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize string for use as filename."""
        if not name:
            return "Unknown"
        # Remove/replace invalid characters
        name = re.sub(r'[<>:"/\\|?*]', "", name)
        name = name.strip(". ")
        return name or "Unknown"

    def _enrich_from_openlibrary(self, metadata: Dict) -> Dict:
        """Look up additional metadata from OpenLibrary."""
        enriched = {}

        if not self.ol_client:
            return enriched

        title = metadata.get("title", "")
        author = metadata.get("author", "")

        if not title:
            return enriched

        try:
            results = self.ol_client.search(title=title, author=author, limit=3)

            if results:
                best = results[0]
                work_key = best.get("key", "")

                if work_key:
                    work = self.ol_client.get_work(work_key)
                    if work:
                        enriched["subjects"] = work.subjects[:10]  # Limit subjects
                        if work.first_publish_year and not metadata.get("year"):
                            enriched["year"] = work.first_publish_year

                # Get ISBN
                isbn_list = best.get("isbn", [])
                if isbn_list:
                    enriched["isbn"] = isbn_list[0]

        except Exception as e:
            if self.verbose:
                print(f"OpenLibrary lookup failed: {e}")

        return enriched

    def _print_summary(self, result: Dict, metadata: Dict, output_file: Path):
        """Print processing summary."""
        print()
        print("=" * 70)
        print(f"Processing: {Path(result['input']).name}")
        print("=" * 70)

        print(f"\nInput: {result['input']}")
        print(f"Chapters Found: {result['chapters']} MP3 files")

        hours = int(metadata.get("duration_hours", 0))
        minutes = int((metadata.get("duration_hours", 0) % 1) * 60)
        print(f"Total Duration: {hours}h {minutes}m")

        print("\nMetadata:")
        print(f"  Title: {metadata.get('title', 'Unknown')}")
        print(f"  Author: {metadata.get('author', 'Unknown')}")
        if metadata.get("narrator"):
            print(f"  Narrator: {metadata.get('narrator')}")
        if metadata.get("genre"):
            print(f"  Genre: {metadata.get('genre')}")
        if metadata.get("year"):
            print(f"  Year: {metadata.get('year')}")
        if metadata.get("has_cover"):
            print("  Cover Art: Found")

        if metadata.get("subjects"):
            print(f"\nOpenLibrary Subjects: {', '.join(metadata['subjects'][:5])}")

        print(f"\nOutput: {output_file}")

        if self.dry_run:
            print()
            print("=" * 70)
            print("DRY RUN - No changes made")
            print("=" * 70)
            print("Run with --execute to apply changes")


def calculate_file_hash(file_path: Path) -> Optional[str]:
    """Calculate SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8 * 1024 * 1024):  # 8MB chunks
                sha256.update(chunk)
        return sha256.hexdigest()
    except (IOError, OSError) as e:
        print(f"Error calculating hash: {e}")
        return None


def import_to_database(
    metadata: Dict, file_path: Path, covers_dir: Path
) -> Optional[int]:
    """Import processed audiobook to database."""
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return None

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Calculate file size
        file_size_mb = file_path.stat().st_size / (1024 * 1024)

        # Calculate SHA-256 hash
        print("Calculating SHA-256 hash...")
        file_hash = calculate_file_hash(file_path)

        cursor.execute(
            """
            INSERT INTO audiobooks (
                title, author, narrator, duration_hours, file_path,
                file_size_mb, published_year, source, isbn,
                format, acquired_date, cover_path, sha256_hash, hash_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                metadata.get("title"),
                metadata.get("author"),
                metadata.get("narrator"),
                metadata.get("duration_hours"),
                str(file_path),
                round(file_size_mb, 2),
                metadata.get("year"),
                metadata.get("source", "other"),
                metadata.get("isbn"),
                "opus",
                datetime.now().strftime("%Y-%m-%d"),
                metadata.get("cover_path"),
                file_hash,
                datetime.now().isoformat() if file_hash else None,
            ),
        )

        audiobook_id = cursor.lastrowid
        conn.commit()

        print(f"Imported to database with ID: {audiobook_id}")
        if file_hash:
            print(f"SHA-256: {file_hash[:16]}...")
        return audiobook_id

    except sqlite3.IntegrityError as e:
        print(f"Database error (may already exist): {e}")
        return None
    finally:
        conn.close()


def main():
    parser = ArgumentParser(
        description="Process Google Play audiobook downloads into OPUS files"
    )
    parser.add_argument(
        "input", type=Path, help="ZIP file or directory containing chapter MP3s"
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=LIBRARY_PATH,
        help=f"Output directory (default: {LIBRARY_PATH})",
    )
    parser.add_argument(
        "--no-enrich", action="store_true", help="Skip OpenLibrary metadata enrichment"
    )
    parser.add_argument(
        "--import-db", action="store_true", help="Import to database after processing"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually process files (default is dry run)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show verbose output"
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: Input not found: {args.input}")
        sys.exit(1)

    # Check for FFmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: FFmpeg not found. Please install FFmpeg.")
        sys.exit(1)

    processor = GooglePlayProcessor(
        output_dir=args.output_dir,
        covers_dir=COVERS_PATH,
        enrich_metadata=not args.no_enrich,
        dry_run=not args.execute,
        verbose=args.verbose,
    )

    result = processor.process(args.input)

    if result["error"]:
        print(f"\nError: {result['error']}")
        sys.exit(1)

    # Import to database if requested
    if args.import_db and args.execute and result["success"]:
        import_to_database(result["metadata"], Path(result["output_path"]), COVERS_PATH)


if __name__ == "__main__":
    main()
