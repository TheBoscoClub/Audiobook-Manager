"""Hybrid metadata lookup orchestrator.

Priority order:
1. Admin override (already in DB)
2. Douban Books lookup by ISBN or title+author
3. DeepL translation fallback
"""

import logging
from dataclasses import dataclass

from .douban import DoubanClient
from ..translation.deepl_translate import DeepLTranslator

logger = logging.getLogger(__name__)


@dataclass
class BookMetadata:
    """Translated book metadata."""

    title: str
    author_display: str
    translator: str  # book translator name, not "how we translated"
    source: str  # "admin", "douban", "deepl"


class MetadataLookup:
    """Resolve localized book metadata using a tiered lookup strategy."""

    def __init__(
        self,
        douban_client: DoubanClient | None = None,
        deepl_translator: DeepLTranslator | None = None,
    ):
        self._douban = douban_client
        self._deepl = deepl_translator

    def lookup(
        self,
        title: str,
        author: str,
        target_locale: str,
        isbn: str = "",
    ) -> BookMetadata | None:
        """Look up localized metadata for a book.

        Tries Douban first (if configured), then falls back to DeepL translation.
        Returns None if no translation source is available.
        """
        # Try Douban by ISBN first, then by title
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

        # Fall back to DeepL machine translation
        if self._deepl:
            try:
                translated = self._deepl.translate(
                    [title, author],
                    target_locale=target_locale,
                )
                return BookMetadata(
                    title=translated[0],
                    author_display=translated[1],
                    translator="",
                    source="deepl",
                )
            except Exception:
                logger.warning("DeepL translation failed for '%s'", title)

        return None
