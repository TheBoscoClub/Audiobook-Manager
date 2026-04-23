"""Open Library enrichment provider.

Last-resort fallback for series and bibliographic metadata. Uses the Open
Library search API — rate-limited more conservatively (1 s between calls)
because the service is rate-sensitive.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.enrichment.base import EnrichmentProvider

_OL_SEARCH_API = "https://openlibrary.org/search.json"
_last_call_time: float = 0.0
_RATE_LIMIT_DELAY: float = 1.0


def _rate_limit() -> None:
    """Enforce minimum delay between Open Library API calls."""
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _RATE_LIMIT_DELAY:
        time.sleep(_RATE_LIMIT_DELAY - elapsed)
    _last_call_time = time.monotonic()


def _search_openlibrary(title: str, author: str) -> dict | None:
    """Search Open Library and return the best-matching doc."""
    _rate_limit()
    params: dict[str, str] = {"limit": "3"}
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    if "title" not in params:
        return None

    url = f"{_OL_SEARCH_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(  # noqa: S310 — Request for fixed HTTPS Open Library API; no user-controlled scheme
        url, headers={"User-Agent": "AudiobookManager/1.0 (library enrichment)"}
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # Reason: URL built from trusted HTTPS constant (_OL_SEARCH_API) + urlencode-escaped search params; not user-controlled scheme
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310  # nosec B310
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):  # fmt: skip
        return None

    docs = data.get("docs", [])
    if not docs:
        return None

    # Prefer exact title match
    for doc in docs:
        if doc.get("title", "").lower().strip() == title.lower().strip():
            return doc
    return docs[0]


def _extract_series_from_doc(doc: dict) -> tuple[str, float | None]:
    """Extract series info from Open Library search result."""
    # OL has a 'series' field sometimes
    series_list = doc.get("series", [])
    if series_list:
        raw = series_list[0] if isinstance(series_list, list) else str(series_list)
        # Try to parse "Series Name #N" or "Series Name, Book N"
        m = re.search(r"(.+?),?\s*(?:#|Book|Vol\.?|Volume)\s*(\d+(?:\.\d+)?)", raw, re.IGNORECASE)
        if m:
            return (m.group(1).strip(), float(m.group(2)))
        return (str(raw).strip(), None)
    return ("", None)


def _apply_ol_series(result: dict, book: dict, doc: dict) -> None:
    """Fill series / series_sequence if the book doesn't already have them."""
    if book.get("series"):
        return
    series_name, seq = _extract_series_from_doc(doc)
    if series_name:
        result["series"] = series_name
        if seq is not None:
            result["series_sequence"] = seq


def _apply_ol_isbn(result: dict, book: dict, doc: dict) -> None:
    """Choose a preferred ISBN (13-digit if available)."""
    isbn_list = doc.get("isbn", [])
    if not isbn_list or book.get("isbn"):
        return
    isbn13 = [i for i in isbn_list if len(str(i)) == 13]
    result["isbn"] = isbn13[0] if isbn13 else isbn_list[0]


def _apply_ol_scalars(result: dict, book: dict, doc: dict) -> None:
    """Fill simple one-off fields (year, publisher, page count, subjects)."""
    if doc.get("first_publish_year") and not book.get("published_year"):
        result["published_year"] = doc["first_publish_year"]

    subjects = doc.get("subject", [])
    if subjects:
        result["ol_subjects"] = subjects[:20]  # Limit to top 20

    publishers = doc.get("publisher", [])
    if publishers and not book.get("publisher"):
        result["publisher"] = publishers[0]

    if doc.get("number_of_pages_median") and not book.get("page_count"):
        result["page_count"] = doc["number_of_pages_median"]


def _apply_ol_cover(result: dict, doc: dict) -> None:
    """Materialize the Open Library cover URL if a cover ID is present."""
    cover_i = doc.get("cover_i")
    if cover_i:
        result["ol_cover_url"] = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"


class OpenLibraryProvider(EnrichmentProvider):
    """Enrichment provider backed by the Open Library search API."""

    name = "openlibrary"

    def can_enrich(self, book: dict) -> bool:
        """Open Library needs at least a title."""
        return bool(book.get("title"))

    def enrich(self, book: dict) -> dict:
        """Search Open Library and return metadata for empty fields."""
        title = book.get("title", "")
        author = book.get("author", "")
        if not title:
            return {}

        doc = _search_openlibrary(title, author)
        if not doc:
            return {}

        result: dict = {}
        _apply_ol_series(result, book, doc)
        _apply_ol_isbn(result, book, doc)
        _apply_ol_scalars(result, book, doc)
        _apply_ol_cover(result, doc)
        return result
