"""Google Books enrichment provider.

Searches Google Books API by title + author when series is still unknown
after the Audible provider. Fills: series, isbn, description, language,
published_date, published_year, categories, publisher, page_count, thumbnail.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.enrichment.base import EnrichmentProvider

_GOOGLE_API = "https://www.googleapis.com/books/v1/volumes"
_last_call_time: float = 0.0
_RATE_LIMIT_DELAY: float = 0.5


def _rate_limit() -> None:
    """Enforce minimum delay between Google Books API calls."""
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _RATE_LIMIT_DELAY:
        time.sleep(_RATE_LIMIT_DELAY - elapsed)
    _last_call_time = time.monotonic()


def _search_google_books(title: str, author: str) -> dict | None:
    """Search Google Books by title and author, return first match."""
    _rate_limit()
    query_parts = []
    if title:
        query_parts.append(f"intitle:{title}")
    if author:
        query_parts.append(f"inauthor:{author}")
    if not query_parts:
        return None

    params = urllib.parse.urlencode(
        {"q": "+".join(query_parts), "maxResults": "3", "printType": "books"}
    )
    url = f"{_GOOGLE_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AudiobookManager/1.0"})
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(  # nosec B310 - fixed HTTPS API URLs (Google Books); no user-controlled scheme
            req, timeout=10
        ) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError, urllib.error.HTTPError, TimeoutError:
        return None

    items = data.get("items", [])
    if not items:
        return None

    # Prefer exact title match
    for item in items:
        vol = item.get("volumeInfo", {})
        if vol.get("title", "").lower().strip() == title.lower().strip():
            return vol
    return items[0].get("volumeInfo", {})


def _extract_series_from_volume(vol: dict) -> tuple[str, float | None]:
    """Try to extract series info from Google Books volume data."""
    # Google Books sometimes has series info in the title or subtitle
    for field in ("subtitle", "title"):
        text = vol.get(field, "")
        if not text:
            continue
        # Pattern: "Series Name, Book N" or "Series Name #N"
        m = re.search(r"(.+?),?\s+(?:Book|Volume|#)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            return (m.group(1).strip(), float(m.group(2)))
        # Pattern: "A SeriesName Novel"
        m = re.search(r"(?:A\s+)?(.+?)\s+Novel\s*$", text, re.IGNORECASE)
        if m:
            return (m.group(1).strip(), None)
    return ("", None)


def _apply_series_from_volume(result: dict, book: dict, vol: dict) -> None:
    """Populate series / series_sequence from a Google Books volume."""
    if book.get("series"):
        return
    series_name, seq = _extract_series_from_volume(vol)
    if series_name:
        result["series"] = series_name
        if seq is not None:
            result["series_sequence"] = seq


def _apply_isbn_from_volume(result: dict, vol: dict) -> None:
    """Pick the best ISBN (prefer ISBN_13 over ISBN_10)."""
    for ident in vol.get("industryIdentifiers", []):
        if ident.get("type") == "ISBN_13":
            result["isbn"] = ident["identifier"]
            return
        if ident.get("type") == "ISBN_10" and "isbn" not in result:
            result["isbn"] = ident["identifier"]


def _apply_simple_fields_from_volume(result: dict, book: dict, vol: dict) -> None:
    """Copy description/language/categories/publisher/page_count when appropriate."""
    if vol.get("description") and not book.get("publisher_summary"):
        result["description"] = vol["description"]
    if vol.get("language") and not book.get("language"):
        result["language"] = vol["language"]
    if vol.get("categories"):
        result["google_categories"] = vol["categories"]
    if vol.get("publisher"):
        result["publisher"] = vol["publisher"]
    if vol.get("pageCount"):
        result["page_count"] = vol["pageCount"]


def _apply_publish_date_from_volume(result: dict, vol: dict) -> None:
    """Extract published_date and published_year."""
    pub_date = vol.get("publishedDate")
    if not pub_date:
        return
    result["published_date"] = pub_date
    m = re.match(r"(\d{4})", pub_date)
    if m:
        result["published_year"] = int(m.group(1))


def _apply_thumbnail_from_volume(result: dict, vol: dict) -> None:
    """Pick the highest-resolution thumbnail available."""
    images = vol.get("imageLinks", {})
    for size in ("extraLarge", "large", "medium", "thumbnail", "smallThumbnail"):
        if size in images:
            result["google_thumbnail"] = images[size]
            return


class GoogleBooksProvider(EnrichmentProvider):
    """Enrichment provider backed by the Google Books API."""

    name = "google_books"

    def can_enrich(self, book: dict) -> bool:
        """Google Books needs at least a title to search."""
        return bool(book.get("title"))

    def enrich(self, book: dict) -> dict:
        """Search Google Books and return metadata for empty fields."""
        title = book.get("title", "")
        author = book.get("author", "")
        if not title:
            return {}

        vol = _search_google_books(title, author)
        if not vol:
            return {}

        result: dict = {}
        _apply_series_from_volume(result, book, vol)
        _apply_isbn_from_volume(result, vol)
        _apply_simple_fields_from_volume(result, book, vol)
        _apply_publish_date_from_volume(result, vol)
        _apply_thumbnail_from_volume(result, vol)
        return result
