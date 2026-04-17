"""Douban Books API client for Chinese book metadata.

Note: Douban's public API has been restricted since 2019.
API keys are difficult to obtain. This module is implemented
for completeness but may not be usable without a valid key.
"""

import logging

import requests

logger = logging.getLogger(__name__)

DOUBAN_API_URL = "https://api.douban.com/v2/book"


class DoubanClient:
    """Look up Chinese book metadata from Douban Books."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    def search_by_isbn(self, isbn: str) -> dict | None:
        """Look up a book by ISBN. Returns metadata dict or None."""
        if not self._api_key:
            logger.debug("Douban API key not configured — skipping lookup")
            return None

        try:
            resp = requests.get(
                f"{DOUBAN_API_URL}/isbn/{isbn}",
                params={"apikey": self._api_key},
                timeout=10,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return {
                "title": data.get("title", ""),
                "author": ", ".join(data.get("author", [])),
                "translator": ", ".join(data.get("translator", [])),
                "source": "douban",
            }
        except Exception:
            logger.warning("Douban lookup failed for ISBN %s", isbn)
            return None

    def search_by_title(self, title: str, author: str = "") -> dict | None:
        """Search for a book by title (and optionally author)."""
        if not self._api_key:
            return None

        query = title
        if author:
            query = f"{title} {author}"

        try:
            resp = requests.get(
                f"{DOUBAN_API_URL}/search",
                params={"q": query, "count": "1", "apikey": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            books = data.get("books", [])
            if not books:
                return None

            book = books[0]
            return {
                "title": book.get("title", ""),
                "author": ", ".join(book.get("author", [])),
                "translator": ", ".join(book.get("translator", [])),
                "source": "douban",
            }
        except Exception:
            logger.warning("Douban search failed for '%s'", title)
            return None
