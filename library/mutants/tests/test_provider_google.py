"""Tests for the Google Books enrichment provider.

All API calls are mocked — no network access during tests.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment.provider_google import GoogleBooksProvider, _extract_series_from_volume


class TestGoogleBooksProviderCanEnrich:
    def test_can_enrich_with_title(self):
        provider = GoogleBooksProvider()
        assert provider.can_enrich({"title": "Some Book"}) is True

    def test_cannot_enrich_without_title(self):
        provider = GoogleBooksProvider()
        assert provider.can_enrich({}) is False
        assert provider.can_enrich({"title": ""}) is False


class TestGoogleBooksProviderEnrich:
    @patch("scripts.enrichment.provider_google._search_google_books")
    def test_returns_empty_when_no_results(self, mock_search):
        mock_search.return_value = None
        provider = GoogleBooksProvider()
        result = provider.enrich({"title": "Nonexistent Book"})
        assert result == {}

    @patch("scripts.enrichment.provider_google._search_google_books")
    def test_extracts_isbn(self, mock_search):
        mock_search.return_value = {
            "industryIdentifiers": [
                {"type": "ISBN_13", "identifier": "9781234567890"},
                {"type": "ISBN_10", "identifier": "1234567890"},
            ]
        }
        provider = GoogleBooksProvider()
        result = provider.enrich({"title": "Some Book"})
        assert result["isbn"] == "9781234567890"

    @patch("scripts.enrichment.provider_google._search_google_books")
    def test_extracts_series_from_subtitle(self, mock_search):
        mock_search.return_value = {"subtitle": "The Dark Tower, Book 3"}
        provider = GoogleBooksProvider()
        result = provider.enrich({"title": "The Waste Lands"})
        assert result["series"] == "The Dark Tower"
        assert result["series_sequence"] == 3.0

    @patch("scripts.enrichment.provider_google._search_google_books")
    def test_skips_series_when_already_set(self, mock_search):
        mock_search.return_value = {"subtitle": "The Dark Tower, Book 3"}
        provider = GoogleBooksProvider()
        result = provider.enrich({"title": "Some Book", "series": "Existing"})
        assert "series" not in result

    @patch("scripts.enrichment.provider_google._search_google_books")
    def test_extracts_metadata(self, mock_search):
        mock_search.return_value = {
            "language": "en",
            "publisher": "Penguin",
            "publishedDate": "2020-05-15",
            "pageCount": 350,
            "categories": ["Fiction", "Thriller"],
            "imageLinks": {"medium": "https://example.com/cover.jpg"},
        }
        provider = GoogleBooksProvider()
        result = provider.enrich({"title": "Some Book"})
        assert result["language"] == "en"
        assert result["publisher"] == "Penguin"
        assert result["published_date"] == "2020-05-15"
        assert result["published_year"] == 2020
        assert result["page_count"] == 350
        assert result["google_categories"] == ["Fiction", "Thriller"]
        assert result["google_thumbnail"] == "https://example.com/cover.jpg"


class TestSeriesExtraction:
    def test_book_number_pattern(self):
        vol = {"subtitle": "Jack Reacher, Book 5"}
        series, seq = _extract_series_from_volume(vol)
        assert series == "Jack Reacher"
        assert seq == 5.0

    def test_volume_pattern(self):
        vol = {"subtitle": "Chronicles Volume 2"}
        series, seq = _extract_series_from_volume(vol)
        assert series == "Chronicles"
        assert seq == 2.0

    def test_novel_pattern(self):
        vol = {"subtitle": "A Reacher Novel"}
        series, seq = _extract_series_from_volume(vol)
        assert series == "Reacher"
        assert seq is None

    def test_no_series(self):
        vol = {"subtitle": "A Memoir"}
        series, seq = _extract_series_from_volume(vol)
        assert series == ""
        assert seq is None
