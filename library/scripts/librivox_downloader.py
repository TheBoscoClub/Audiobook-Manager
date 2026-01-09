#!/usr/bin/env python3
"""
================================================================================
WARNING: EXPERIMENTAL / NOT FULLY TESTED - USE AT YOUR OWN RISK
================================================================================
This script downloads audiobooks from Librivox (public domain MP3s).
This format is NOT fully tested and may not work as expected.

KNOWN ISSUES:
- Inconsistent file naming across different Librivox releases
- Metadata is often minimal or missing
- Multi-reader audiobooks may not be handled correctly
- Chapter ordering may be incorrect for some releases
- ZIP extraction may fail for certain archive formats

The ONLY fully tested and verified format is Audible's AAXC format, which is
handled by the main audiobook conversion pipeline (convert-audiobooks-opus-parallel,
download-new-audiobooks, etc.)

This script is part of the multi-source audiobook support feature which has been
moved to "Phase Maybe" in the roadmap. The code exists and may work, but it is
not actively supported or prioritized.

If you want to use or finish this feature, you're welcome to - PRs accepted.
================================================================================

Librivox Audiobook Downloader

Download free public domain audiobooks from Librivox.org.
Supports searching by title, author, or browsing recent additions.

Usage:
    # Search and download interactively
    python3 librivox_downloader.py --search "pride and prejudice"

    # Download specific audiobook by ID
    python3 librivox_downloader.py --id 12345

    # List recent audiobooks
    python3 librivox_downloader.py --recent

    # Download from wishlist file
    python3 librivox_downloader.py --wishlist ~/librivox-wishlist.txt

API Documentation: https://librivox.org/api/info
"""

import os
import re
import requests
import sys
import time
from pathlib import Path
from argparse import ArgumentParser
from typing import Optional, List, Dict
from dataclasses import dataclass
from xml.etree import ElementTree

# Add parent directory for config
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from config import AUDIOBOOKS_DATA

    DEFAULT_OUTPUT = AUDIOBOOKS_DATA / "Sources-Librivox"
except ImportError:
    # Fallback to environment variables when running standalone
    _data_dir = os.environ.get("AUDIOBOOKS_DATA", "/srv/audiobooks")
    DEFAULT_OUTPUT = Path(_data_dir) / "Sources-Librivox"

LIBRIVOX_API = "https://librivox.org/api/feed/audiobooks"


@dataclass
class LibrivoxBook:
    """Represents a Librivox audiobook."""

    id: str
    title: str
    author: str
    description: str
    url_librivox: str
    url_rss: str
    url_zip: Optional[str]
    language: str
    copyright_year: Optional[int]
    num_sections: int
    total_time: str
    sections: List[Dict] = None

    def __post_init__(self):
        if self.sections is None:
            self.sections = []


class LibrivoxDownloader:
    """Download audiobooks from Librivox."""

    def __init__(self, output_dir: Path = DEFAULT_OUTPUT, verbose: bool = False):
        self.output_dir = Path(output_dir)
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "AudiobookLibrary/1.0 (personal audiobook manager)"}
        )

    def search(
        self, title: Optional[str] = None, author: Optional[str] = None, limit: int = 10
    ) -> List[LibrivoxBook]:
        """
        Search Librivox for audiobooks.

        Args:
            title: Title to search for
            author: Author to search for
            limit: Maximum results

        Returns:
            List of matching LibrivoxBook objects
        """
        params = {"format": "json", "limit": limit}

        # Librivox API uses ^ for exact match prefix, but often fails
        # Use simple search terms instead
        if title:
            params["title"] = title
        if author:
            params["author"] = author

        try:
            response = self.session.get(LIBRIVOX_API, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if "books" not in data:
                return []

            books = []
            for book_data in data["books"]:
                books.append(self._parse_book(book_data))

            return books

        except requests.RequestException as e:
            print(f"API error: {e}")
            return []

    def get_recent(self, limit: int = 20) -> List[LibrivoxBook]:
        """Get recently added audiobooks."""
        params = {"format": "json", "limit": limit, "sort": "release_date"}

        try:
            response = self.session.get(LIBRIVOX_API, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if "books" not in data:
                return []

            return [self._parse_book(b) for b in data["books"]]

        except requests.RequestException as e:
            print(f"API error: {e}")
            return []

    def get_by_id(self, book_id: str) -> Optional[LibrivoxBook]:
        """Get audiobook by Librivox ID."""
        params = {"format": "json", "id": book_id}

        try:
            response = self.session.get(LIBRIVOX_API, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if "books" not in data or not data["books"]:
                return None

            return self._parse_book(data["books"][0])

        except requests.RequestException as e:
            print(f"API error: {e}")
            return None

    def download(self, book: LibrivoxBook, use_zip: bool = True) -> Optional[Path]:
        """
        Download an audiobook.

        Args:
            book: LibrivoxBook to download
            use_zip: If True, download ZIP archive; otherwise download individual MP3s

        Returns:
            Path to downloaded directory/file, or None on failure
        """
        # Create output directory
        safe_title = self._sanitize_filename(book.title)
        safe_author = self._sanitize_filename(book.author)
        book_dir = self.output_dir / f"{safe_author} - {safe_title}"

        # Check if already downloaded
        if book_dir.exists() and any(book_dir.iterdir()):
            print(f"Already downloaded: {book_dir}")
            return book_dir

        book_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata
        self._save_metadata(book, book_dir)

        if use_zip and book.url_zip:
            # Download ZIP archive
            zip_path = book_dir / f"{safe_title}.zip"
            if self._download_file(book.url_zip, zip_path):
                # Extract ZIP
                try:
                    import zipfile

                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(book_dir)
                    zip_path.unlink()  # Remove ZIP after extraction
                    print(f"Downloaded and extracted: {book_dir}")
                    return book_dir
                except Exception as e:
                    print(f"Failed to extract ZIP: {e}")
                    return None
        else:
            # Download individual sections from RSS
            if self._download_sections(book, book_dir):
                print(f"Downloaded {len(book.sections)} sections to: {book_dir}")
                return book_dir

        return None

    def _download_file(self, url: str, output_path: Path) -> bool:
        """Download a file with progress."""
        try:
            response = self.session.get(url, stream=True, timeout=300)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size and self.verbose:
                        pct = (downloaded / total_size) * 100
                        print(f"\r  Downloading: {pct:.1f}%", end="", flush=True)

            if self.verbose:
                print()
            return True

        except requests.RequestException as e:
            print(f"Download failed: {e}")
            return False

    def _download_sections(self, book: LibrivoxBook, output_dir: Path) -> bool:
        """Download individual sections from RSS feed."""
        if not book.sections:
            # Fetch sections from RSS
            book.sections = self._get_sections_from_rss(book.url_rss)

        if not book.sections:
            print("No sections found")
            return False

        success_count = 0
        for i, section in enumerate(book.sections, 1):
            url = section.get("url")
            title = section.get("title", f"Section {i:02d}")
            safe_title = self._sanitize_filename(title)

            output_file = output_dir / f"{i:02d} - {safe_title}.mp3"

            if output_file.exists():
                print(f"  Skipping (exists): {output_file.name}")
                success_count += 1
                continue

            print(f"  [{i}/{len(book.sections)}] Downloading: {title}")
            if self._download_file(url, output_file):
                success_count += 1
                time.sleep(1)  # Rate limiting
            else:
                print(f"  Failed: {title}")

        return success_count > 0

    def _get_sections_from_rss(self, rss_url: str) -> List[Dict]:
        """Parse RSS feed to get section URLs."""
        try:
            response = self.session.get(rss_url, timeout=30)
            response.raise_for_status()

            root = ElementTree.fromstring(response.content)
            sections = []

            for item in root.findall(".//item"):
                title_elem = item.find("title")
                enclosure = item.find("enclosure")

                if enclosure is not None:
                    sections.append(
                        {
                            "title": title_elem.text if title_elem is not None else "",
                            "url": enclosure.get("url", ""),
                            "length": enclosure.get("length", 0),
                        }
                    )

            return sections

        except Exception as e:
            print(f"RSS parse error: {e}")
            return []

    def _parse_book(self, data: Dict) -> LibrivoxBook:
        """Parse API response into LibrivoxBook."""
        return LibrivoxBook(
            id=data.get("id", ""),
            title=data.get("title", "Unknown Title"),
            author=self._extract_authors(data.get("authors", [])),
            description=data.get("description", ""),
            url_librivox=data.get("url_librivox", ""),
            url_rss=data.get("url_rss", ""),
            url_zip=data.get("url_zip_file"),
            language=data.get("language", "English"),
            copyright_year=self._parse_year(data.get("copyright_year")),
            num_sections=int(data.get("num_sections", 0)),
            total_time=data.get("totaltime", ""),
        )

    def _extract_authors(self, authors_data: List) -> str:
        """Extract author names from API response."""
        if not authors_data:
            return "Unknown Author"

        names = []
        for author in authors_data:
            if isinstance(author, dict):
                first = author.get("first_name", "")
                last = author.get("last_name", "")
                name = f"{first} {last}".strip()
                if name:
                    names.append(name)
            elif isinstance(author, str):
                names.append(author)

        return ", ".join(names) if names else "Unknown Author"

    def _parse_year(self, year_str) -> Optional[int]:
        """Parse year from string."""
        if not year_str:
            return None
        try:
            # Handle ranges like "1813-1814"
            match = re.match(r"(\d{4})", str(year_str))
            if match:
                return int(match.group(1))
        except ValueError:
            pass
        return None

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize string for filename."""
        if not name:
            return "Unknown"
        name = re.sub(r'[<>:"/\\|?*]', "", name)
        name = name.strip(". ")
        return name[:100] or "Unknown"  # Limit length

    def _save_metadata(self, book: LibrivoxBook, output_dir: Path):
        """Save book metadata to file."""
        metadata_file = output_dir / "librivox_metadata.txt"
        with open(metadata_file, "w") as f:
            f.write(f"Title: {book.title}\n")
            f.write(f"Author: {book.author}\n")
            f.write(f"Librivox ID: {book.id}\n")
            f.write(f"Language: {book.language}\n")
            f.write(f"Sections: {book.num_sections}\n")
            f.write(f"Total Time: {book.total_time}\n")
            f.write(f"Copyright Year: {book.copyright_year}\n")
            f.write(f"URL: {book.url_librivox}\n")
            f.write(f"\nDescription:\n{book.description}\n")


def interactive_search(downloader: LibrivoxDownloader, query: str):
    """Interactive search and download."""
    print(f"\nSearching for: {query}")
    print("-" * 60)

    books = downloader.search(title=query, limit=10)

    if not books:
        print("No results found.")
        return

    for i, book in enumerate(books, 1):
        print(f"\n{i}. {book.title}")
        print(f"   Author: {book.author}")
        print(f"   Duration: {book.total_time} ({book.num_sections} sections)")
        print(f"   Language: {book.language}")

    print("\n" + "-" * 60)
    choice = input("Enter number to download (or 'q' to quit): ").strip()

    if choice.lower() == "q":
        return

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(books):
            book = books[idx]
            print(f"\nDownloading: {book.title}")
            result = downloader.download(book)
            if result:
                print(f"\nSuccess! Downloaded to: {result}")
        else:
            print("Invalid selection.")
    except ValueError:
        print("Invalid input.")


def main():
    parser = ArgumentParser(description="Download free audiobooks from Librivox")
    parser.add_argument(
        "--search", "-s", type=str, help="Search for audiobook by title"
    )
    parser.add_argument(
        "--author", "-a", type=str, help="Filter by author (use with --search)"
    )
    parser.add_argument(
        "--id", type=str, help="Download specific audiobook by Librivox ID"
    )
    parser.add_argument("--recent", action="store_true", help="List recent audiobooks")
    parser.add_argument(
        "--wishlist",
        "-w",
        type=Path,
        help="Download from wishlist file (one title per line)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--individual",
        action="store_true",
        help="Download individual MP3s instead of ZIP",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    downloader = LibrivoxDownloader(output_dir=args.output_dir, verbose=args.verbose)

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.id:
        # Download by ID
        print(f"Fetching audiobook ID: {args.id}")
        book = downloader.get_by_id(args.id)
        if book:
            print(f"Found: {book.title} by {book.author}")
            result = downloader.download(book, use_zip=not args.individual)
            if result:
                print(f"Downloaded to: {result}")
        else:
            print("Audiobook not found.")

    elif args.recent:
        # List recent
        print("Recent Librivox Audiobooks:")
        print("-" * 60)
        books = downloader.get_recent(20)
        for book in books:
            print(f"\n{book.title}")
            print(f"  Author: {book.author}")
            print(f"  Duration: {book.total_time}")
            print(f"  ID: {book.id}")

    elif args.wishlist:
        # Download from wishlist
        if not args.wishlist.exists():
            print(f"Wishlist not found: {args.wishlist}")
            sys.exit(1)

        with open(args.wishlist) as f:
            titles = [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]

        print(f"Processing wishlist with {len(titles)} titles...")
        for title in titles:
            print(f"\nSearching: {title}")
            books = downloader.search(title=title, limit=1)
            if books:
                book = books[0]
                print(f"  Found: {book.title} by {book.author}")
                downloader.download(book, use_zip=not args.individual)
            else:
                print(f"  Not found: {title}")
            time.sleep(2)  # Rate limiting

    elif args.search:
        # Interactive search
        interactive_search(downloader, args.search)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
