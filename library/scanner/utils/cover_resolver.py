"""
External cover art resolver for audiobooks missing embedded covers.

Tiered lookup strategy:
  1. Audible API (by ASIN) — highest quality, most audiobook-specific
  2. Open Library API (by title/author) — free, no key needed
  3. Google Books API (by title/author) — free tier, good fallback

Called by extract_cover_art() in metadata_utils.py when local extraction
(ffmpeg embedded + standalone sidecar) finds nothing.
"""

import hashlib
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

# Reuse the existing OpenLibrary client.
# Scanner runs with `library/` on sys.path (see scan_audiobooks.py), so the
# canonical module path is `scripts.utils.openlibrary_client`. The previous
# `utils.openlibrary_client` was unresolvable and silently disabled the
# external cover-art fallback (masked by an `except ImportError` upstream).
from scripts.utils.openlibrary_client import OpenLibraryClient

# Rate limiting: shared across all resolvers within a session
_last_request_time = 0.0
_MIN_DELAY = 0.6  # seconds between external API calls


def _rate_limit():
    """Enforce minimum delay between external API calls."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_DELAY:
        time.sleep(_MIN_DELAY - elapsed)
    _last_request_time = time.time()


def resolve_cover(
    title: str,
    author: Optional[str] = None,
    asin: Optional[str] = None,
    output_dir: Optional[Path] = None,
    timeout: int = 15,
) -> Optional[str]:
    """
    Attempt to fetch cover art from external sources.

    Args:
        title: Audiobook title
        author: Author name (optional but improves accuracy)
        asin: Amazon ASIN (optional, enables Audible lookup)
        output_dir: Directory to save the cover image
        timeout: HTTP request timeout in seconds

    Returns:
        Cover filename (e.g., "abc123.jpg") if successful, None otherwise.
    """
    if not output_dir:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    # Tier 1: Audible (by ASIN)
    if asin:
        result = _try_audible(asin, output_dir, timeout)
        if result:
            return result

    # Tier 2: Open Library (by title/author)
    result = _try_openlibrary(title, author, output_dir, timeout)
    if result:
        return result

    # Tier 3: Google Books (by title/author)
    result = _try_google_books(title, author, output_dir, timeout)
    if result:
        return result

    return None


def _save_image(image_data: bytes, output_dir: Path, source_url: str) -> Optional[str]:
    """Save image data to output_dir with MD5-based filename. Returns filename."""
    if not image_data or len(image_data) < 1000:
        # Too small to be a real cover — likely a placeholder/error
        return None

    file_hash = hashlib.md5(image_data, usedforsecurity=False).hexdigest()
    cover_path = output_dir / f"{file_hash}.jpg"

    try:
        cover_path.write_bytes(image_data)
        return cover_path.name
    except OSError as e:
        print(f"Warning: failed to save cover from {source_url}: {e}", file=sys.stderr)
        return None


def _try_audible(asin: str, output_dir: Path, timeout: int) -> Optional[str]:
    """Tier 1: Fetch cover from Audible's image CDN by ASIN."""
    _rate_limit()
    url = f"https://m.media-amazon.com/images/I/{asin}._SL500_.jpg"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            return _save_image(resp.content, output_dir, url)
    except requests.RequestException:
        pass

    # Alternate Audible CDN format
    _rate_limit()
    url2 = f"https://images-na.ssl-images-amazon.com/images/I/{asin}._SL500_.jpg"
    try:
        resp = requests.get(url2, timeout=timeout)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            return _save_image(resp.content, output_dir, url2)
    except requests.RequestException:
        pass

    return None


def _try_openlibrary(
    title: str, author: Optional[str], output_dir: Path, timeout: int
) -> Optional[str]:
    """Tier 2: Search Open Library for cover art."""
    try:
        client = OpenLibraryClient(timeout=timeout)
        results = client.search(title=title, author=author, limit=3)

        for result in results:
            cover_ids = result.get("cover_i")
            if cover_ids:
                cover_id = cover_ids if isinstance(cover_ids, int) else cover_ids
                url = client.get_cover_url(cover_id, size="L")
                _rate_limit()
                resp = requests.get(url, timeout=timeout)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    return _save_image(resp.content, output_dir, url)
    except Exception as e:
        print(f"Warning: Open Library cover lookup failed: {e}", file=sys.stderr)

    return None


def _try_google_books(
    title: str, author: Optional[str], output_dir: Path, timeout: int
) -> Optional[str]:
    """Tier 3: Search Google Books API for cover art (free, no key needed)."""
    _rate_limit()
    query = title
    if author:
        query = f"{title}+inauthor:{author}"

    url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(query)}&maxResults=3"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None

        data = resp.json()
        for item in data.get("items", []):
            image_links = item.get("volumeInfo", {}).get("imageLinks", {})
            # Prefer largest available
            img_url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
            if img_url:
                # Google Books returns http URLs; upgrade and remove curl param
                img_url = img_url.replace("http://", "https://")
                # Request larger image by tweaking the zoom parameter
                img_url = img_url.replace("zoom=1", "zoom=2")
                _rate_limit()
                # nosemgrep: python.lang.security.audit.insecure-transport.requests.request-with-http.request-with-http
                img_resp = requests.get(img_url, timeout=timeout)  # URL upgraded to https above
                if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                    return _save_image(img_resp.content, output_dir, img_url)
    except requests.RequestException:
        pass
    except Exception as e:
        print(f"Warning: Google Books cover lookup failed: {e}", file=sys.stderr)

    return None
