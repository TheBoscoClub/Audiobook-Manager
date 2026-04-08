"""Tests for the Audible enrichment provider.

All API calls are mocked — no network access during tests.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment.provider_audible import (
    AudibleProvider,
    _extract_categories,
    _extract_editorial_reviews,
    _extract_rating,
    _get_best_image_url,
    _parse_sequence,
)


class TestAudibleProviderCanEnrich:
    def test_can_enrich_with_asin(self):
        provider = AudibleProvider()
        assert provider.can_enrich({"asin": "B08G9PRS1K"}) is True

    def test_cannot_enrich_without_asin(self):
        provider = AudibleProvider()
        assert provider.can_enrich({"asin": ""}) is False
        assert provider.can_enrich({}) is False


class TestAudibleProviderEnrich:
    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_returns_empty_when_no_asin(self, mock_fetch):
        provider = AudibleProvider()
        result = provider.enrich({"asin": ""})
        assert result == {}
        mock_fetch.assert_not_called()

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_returns_empty_when_api_returns_none(self, mock_fetch):
        mock_fetch.return_value = None
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result == {}

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_series(self, mock_fetch):
        mock_fetch.return_value = {
            "series": [{"title": "Jack Reacher", "sequence": "3"}],
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result["series"] == "Jack Reacher"
        assert result["series_sequence"] == 3.0

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_skips_series_when_already_set(self, mock_fetch):
        mock_fetch.return_value = {
            "series": [{"title": "Jack Reacher", "sequence": "3"}],
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K", "series": "Existing"})
        assert "series" not in result

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_ratings(self, mock_fetch):
        mock_fetch.return_value = {
            "rating": {
                "overall_distribution": {
                    "display_average_rating": 4.5,
                    "num_ratings": 1200,
                },
                "performance_distribution": {"display_average_rating": 4.7},
                "story_distribution": {"display_average_rating": 4.3},
                "num_reviews": 350,
            }
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result["rating_overall"] == 4.5
        assert result["rating_performance"] == 4.7
        assert result["rating_story"] == 4.3
        assert result["num_ratings"] == 350
        assert result["num_reviews"] == 1200

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_categories(self, mock_fetch):
        mock_fetch.return_value = {
            "category_ladders": [
                {
                    "ladder": [
                        {"name": "Literature & Fiction", "id": "1234"},
                        {"name": "Thriller", "id": "5678"},
                    ]
                }
            ]
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        cats = result["categories"]
        assert len(cats) == 2
        assert cats[0]["root_category"] == "Literature & Fiction"
        assert cats[1]["category_path"] == "Literature & Fiction > Thriller"

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_image_url(self, mock_fetch):
        mock_fetch.return_value = {
            "product_images": {"500": "https://example.com/500.jpg", "252": "https://example.com/252.jpg"}
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result["audible_image_url"] == "https://example.com/500.jpg"


class TestHelpers:
    def test_parse_sequence_integer(self):
        assert _parse_sequence("5") == 5.0

    def test_parse_sequence_float(self):
        assert _parse_sequence("3.5") == 3.5

    def test_parse_sequence_embedded(self):
        assert _parse_sequence("Book 7") == 7.0

    def test_parse_sequence_empty(self):
        assert _parse_sequence("") is None

    def test_extract_categories_empty(self):
        assert _extract_categories({}) == []

    def test_extract_editorial_reviews(self):
        product = {"editorial_reviews": ["Great book!", {"review": "Amazing", "source": "NYT"}]}
        reviews = _extract_editorial_reviews(product)
        assert len(reviews) == 2
        assert reviews[0]["review_text"] == "Great book!"
        assert reviews[1]["source"] == "NYT"

    def test_extract_rating_empty(self):
        result = _extract_rating({})
        assert result["rating_overall"] is None

    def test_get_best_image_prefers_largest(self):
        images = {"252": "small.jpg", "1024": "large.jpg", "500": "medium.jpg"}
        assert _get_best_image_url({"product_images": images}) == "large.jpg"

    def test_get_best_image_none(self):
        assert _get_best_image_url({}) is None
