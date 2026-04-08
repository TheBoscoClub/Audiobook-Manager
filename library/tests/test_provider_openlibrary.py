"""Tests for the Open Library enrichment provider.

All API calls are mocked — no network access during tests.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment.provider_openlibrary import (
    OpenLibraryProvider,
    _extract_series_from_doc,
)


class TestOpenLibraryProviderCanEnrich:
    def test_can_enrich_with_title(self):
        provider = OpenLibraryProvider()
        assert provider.can_enrich({"title": "Some Book"}) is True

    def test_cannot_enrich_without_title(self):
        provider = OpenLibraryProvider()
        assert provider.can_enrich({}) is False


class TestOpenLibraryProviderEnrich:
    @patch("scripts.enrichment.provider_openlibrary._search_openlibrary")
    def test_returns_empty_when_no_results(self, mock_search):
        mock_search.return_value = None
        provider = OpenLibraryProvider()
        result = provider.enrich({"title": "Nonexistent Book"})
        assert result == {}

    @patch("scripts.enrichment.provider_openlibrary._search_openlibrary")
    def test_extracts_series(self, mock_search):
        mock_search.return_value = {
            "title": "The Gunslinger",
            "series": ["The Dark Tower #1"],
        }
        provider = OpenLibraryProvider()
        result = provider.enrich({"title": "The Gunslinger"})
        assert result["series"] == "The Dark Tower"
        assert result["series_sequence"] == 1.0

    @patch("scripts.enrichment.provider_openlibrary._search_openlibrary")
    def test_skips_series_when_already_set(self, mock_search):
        mock_search.return_value = {
            "title": "Some Book",
            "series": ["Mystery Series #5"],
        }
        provider = OpenLibraryProvider()
        result = provider.enrich({"title": "Some Book", "series": "Existing"})
        assert "series" not in result

    @patch("scripts.enrichment.provider_openlibrary._search_openlibrary")
    def test_extracts_isbn_prefers_13(self, mock_search):
        mock_search.return_value = {
            "title": "Some Book",
            "isbn": ["1234567890", "9781234567890"],
        }
        provider = OpenLibraryProvider()
        result = provider.enrich({"title": "Some Book"})
        assert result["isbn"] == "9781234567890"

    @patch("scripts.enrichment.provider_openlibrary._search_openlibrary")
    def test_extracts_metadata(self, mock_search):
        mock_search.return_value = {
            "title": "Some Book",
            "first_publish_year": 1982,
            "subject": ["Fantasy", "Adventure", "Magic"],
            "publisher": ["Tor Books", "Del Rey"],
            "number_of_pages_median": 450,
            "cover_i": 12345,
        }
        provider = OpenLibraryProvider()
        result = provider.enrich({"title": "Some Book"})
        assert result["published_year"] == 1982
        assert "Fantasy" in result["ol_subjects"]
        assert result["publisher"] == "Tor Books"
        assert result["page_count"] == 450
        assert "12345" in result["ol_cover_url"]


class TestSeriesExtraction:
    def test_hash_format(self):
        doc = {"series": ["Dark Tower #1"]}
        series, seq = _extract_series_from_doc(doc)
        assert series == "Dark Tower"
        assert seq == 1.0

    def test_book_format(self):
        doc = {"series": ["Reacher, Book 5"]}
        series, seq = _extract_series_from_doc(doc)
        assert series == "Reacher"
        assert seq == 5.0

    def test_plain_series_name(self):
        doc = {"series": ["The Expanse"]}
        series, seq = _extract_series_from_doc(doc)
        assert series == "The Expanse"
        assert seq is None

    def test_no_series(self):
        doc = {}
        series, seq = _extract_series_from_doc(doc)
        assert series == ""
        assert seq is None
