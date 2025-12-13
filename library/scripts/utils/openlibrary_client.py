#!/usr/bin/env python3
"""
OpenLibrary API client with rate limiting and retry logic.

Provides a reusable interface for OpenLibrary API operations:
- ISBN lookup
- Title/author search
- Work details retrieval
- Automatic rate limiting (~100 requests/minute)
- Exponential backoff on rate limit errors

Usage:
    from utils.openlibrary_client import OpenLibraryClient

    client = OpenLibraryClient()

    # Lookup by ISBN
    edition = client.lookup_isbn('9780261103573')

    # Search by title/author
    results = client.search(title='The Hobbit', author='Tolkien')

    # Get work details (includes subjects/genres)
    work = client.get_work('OL27479W')
"""

import requests
import time
from typing import Optional, Dict, List
from dataclasses import dataclass, field


@dataclass
class OpenLibraryWork:
    """Parsed work data from OpenLibrary."""
    work_id: str
    title: str
    authors: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)
    first_publish_year: Optional[int] = None
    description: Optional[str] = None
    covers: List[int] = field(default_factory=list)


@dataclass
class OpenLibraryEdition:
    """Parsed edition data from OpenLibrary."""
    key: str
    title: str
    authors: List[str] = field(default_factory=list)
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None
    publish_date: Optional[str] = None
    publishers: List[str] = field(default_factory=list)
    work_id: Optional[str] = None


class RateLimitError(Exception):
    """Raised when API rate limit is hit."""
    pass


class OpenLibraryClient:
    """Client for OpenLibrary API with rate limiting."""

    BASE_URL = "https://openlibrary.org"

    def __init__(self,
                 rate_limit_delay: float = 0.6,
                 timeout: int = 30,
                 max_retries: int = 3):
        """
        Initialize the client.

        Args:
            rate_limit_delay: Seconds between requests (~100/min = 0.6s)
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts on rate limit errors
        """
        self.delay = rate_limit_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'AudiobookLibrary/1.0 (personal audiobook manager; https://github.com/greogory/audiobook-toolkit)'
        })

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_request_time = time.time()

    def _get(self, url: str, retry_count: int = 0) -> Optional[Dict]:
        """
        Make rate-limited GET request with retry logic.

        Args:
            url: Full URL to request
            retry_count: Current retry attempt

        Returns:
            JSON response dict or None on error
        """
        self._rate_limit()

        try:
            response = self.session.get(url, timeout=self.timeout)

            if response.status_code == 429:
                if retry_count < self.max_retries:
                    # Exponential backoff: 2, 4, 8 seconds
                    wait_time = 2 ** (retry_count + 1)
                    time.sleep(wait_time)
                    return self._get(url, retry_count + 1)
                raise RateLimitError("OpenLibrary rate limit exceeded after retries")

            if response.status_code == 404:
                return None

            response.raise_for_status()
            return response.json()

        except requests.Timeout:
            if retry_count < self.max_retries:
                return self._get(url, retry_count + 1)
            return None
        except requests.RequestException as e:
            print(f"API request failed: {e}")
            return None

    def lookup_isbn(self, isbn: str) -> Optional[OpenLibraryEdition]:
        """
        Look up book by ISBN.

        Args:
            isbn: ISBN-10 or ISBN-13

        Returns:
            OpenLibraryEdition or None if not found
        """
        # Clean ISBN (remove hyphens, spaces)
        isbn = isbn.replace('-', '').replace(' ', '')

        url = f"{self.BASE_URL}/isbn/{isbn}.json"
        data = self._get(url)

        if not data:
            return None

        return self._parse_edition(data)

    def search(self,
               title: Optional[str] = None,
               author: Optional[str] = None,
               isbn: Optional[str] = None,
               limit: int = 5) -> List[Dict]:
        """
        Search OpenLibrary by title and/or author.

        Args:
            title: Book title to search
            author: Author name to search
            isbn: ISBN to search
            limit: Maximum results to return

        Returns:
            List of search result dicts with keys: title, author_name,
            key (work ID), first_publish_year, isbn, etc.
        """
        params = []
        if title:
            params.append(f"title={requests.utils.quote(title)}")
        if author:
            params.append(f"author={requests.utils.quote(author)}")
        if isbn:
            params.append(f"isbn={requests.utils.quote(isbn)}")
        params.append(f"limit={limit}")

        if not params:
            return []

        url = f"{self.BASE_URL}/search.json?{'&'.join(params)}"
        data = self._get(url)

        if not data or 'docs' not in data:
            return []

        return data['docs'][:limit]

    def get_work(self, work_id: str) -> Optional[OpenLibraryWork]:
        """
        Get work details including subjects.

        Args:
            work_id: OpenLibrary work ID (e.g., "OL123W" or "/works/OL123W")

        Returns:
            OpenLibraryWork or None if not found
        """
        # Normalize work ID
        if work_id.startswith('/works/'):
            work_id = work_id.replace('/works/', '')

        url = f"{self.BASE_URL}/works/{work_id}.json"
        data = self._get(url)

        if not data:
            return None

        return self._parse_work(data)

    def get_author(self, author_id: str) -> Optional[Dict]:
        """
        Get author details.

        Args:
            author_id: OpenLibrary author ID (e.g., "OL23919A")

        Returns:
            Author data dict or None if not found
        """
        if author_id.startswith('/authors/'):
            author_id = author_id.replace('/authors/', '')

        url = f"{self.BASE_URL}/authors/{author_id}.json"
        return self._get(url)

    def _parse_edition(self, data: Dict) -> OpenLibraryEdition:
        """Parse edition JSON into OpenLibraryEdition."""
        # Extract work ID from works list
        work_id = None
        if 'works' in data and data['works']:
            work_key = data['works'][0].get('key', '')
            work_id = work_key.replace('/works/', '')

        # Extract ISBNs
        isbn_10 = None
        isbn_13 = None
        if data.get('isbn_10'):
            isbn_10 = data['isbn_10'][0] if isinstance(data['isbn_10'], list) else data['isbn_10']
        if data.get('isbn_13'):
            isbn_13 = data['isbn_13'][0] if isinstance(data['isbn_13'], list) else data['isbn_13']

        return OpenLibraryEdition(
            key=data.get('key', ''),
            title=data.get('title', ''),
            authors=[],  # Would need additional lookup via author keys
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            publish_date=data.get('publish_date'),
            publishers=data.get('publishers', []),
            work_id=work_id
        )

    def _parse_work(self, data: Dict) -> OpenLibraryWork:
        """Parse work JSON into OpenLibraryWork."""
        # Extract subjects (can be list of strings or dicts)
        subjects = []
        for subj in data.get('subjects', []):
            if isinstance(subj, str):
                subjects.append(subj)
            elif isinstance(subj, dict):
                name = subj.get('name', '')
                if name:
                    subjects.append(name)

        # Extract description
        description = None
        desc_data = data.get('description')
        if isinstance(desc_data, str):
            description = desc_data
        elif isinstance(desc_data, dict):
            description = desc_data.get('value', '')

        # Extract author names (requires additional lookups in real use)
        authors = []
        for author_ref in data.get('authors', []):
            if isinstance(author_ref, dict):
                author_key = author_ref.get('author', {}).get('key', '')
                if author_key:
                    authors.append(author_key)  # Just the key for now

        return OpenLibraryWork(
            work_id=data.get('key', '').replace('/works/', ''),
            title=data.get('title', ''),
            authors=authors,
            subjects=subjects,
            first_publish_year=data.get('first_publish_year'),
            description=description,
            covers=data.get('covers', [])
        )

    def get_cover_url(self, cover_id: int, size: str = 'M') -> str:
        """
        Get URL for a cover image.

        Args:
            cover_id: OpenLibrary cover ID
            size: Size code - 'S' (small), 'M' (medium), 'L' (large)

        Returns:
            Cover image URL
        """
        return f"https://covers.openlibrary.org/b/id/{cover_id}-{size}.jpg"


# Simple test if run directly
if __name__ == '__main__':
    client = OpenLibraryClient()

    # Test ISBN lookup
    print("Testing ISBN lookup...")
    edition = client.lookup_isbn('9780261103573')
    if edition:
        print(f"  Found: {edition.title}")
        print(f"  Work ID: {edition.work_id}")

        if edition.work_id:
            print("\nTesting work lookup...")
            work = client.get_work(edition.work_id)
            if work:
                print(f"  Title: {work.title}")
                print(f"  Subjects: {', '.join(work.subjects[:5])}")
                print(f"  First published: {work.first_publish_year}")

    # Test search
    print("\nTesting search...")
    results = client.search(title='The Hobbit', author='Tolkien', limit=3)
    for r in results:
        print(f"  - {r.get('title')} by {', '.join(r.get('author_name', ['Unknown']))}")
