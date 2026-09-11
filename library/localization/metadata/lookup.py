"""Hybrid metadata lookup orchestrator.

Priority order:
1. Admin override (already in DB)
2. Douban Books lookup by ISBN or title+author

A machine-translation fallback used to sit behind Douban; it was removed
with the hosted-MT integration (Audiobook-Manager-4uj). If a
TranslationProvider backend returns, restore the fallback against the
provider abstraction — not against a concrete vendor client.
"""

import logging
from dataclasses import dataclass

from .douban import DoubanClient

logger = logging.getLogger(__name__)


@dataclass
class BookMetadata:
    """Translated book metadata."""

    title: str
    author_display: str
    translator: str  # book translator name, not "how we translated"
    source: str  # "admin" or "douban"


class MetadataLookup:
    """Resolve localized book metadata using a tiered lookup strategy."""

    def __init__(self, douban_client: DoubanClient | None = None):
        self._douban = douban_client

    def lookup(
        self, title: str, author: str, target_locale: str, isbn: str = ""
    ) -> BookMetadata | None:
        """Look up localized metadata for a book.

        Tries Douban by ISBN first, then by title+author.
        Returns None if no metadata source is available or nothing matched.
        """
        if self._douban:
            result = None
            if isbn:
                result = self._douban.search_by_isbn(isbn)
            if not result:
                result = self._douban.search_by_title(title, author)
            if result:
                return BookMetadata(
                    title=result["title"],
                    author_display=result["author"],
                    translator=result.get("translator", ""),
                    source="douban",
                )

        return None
