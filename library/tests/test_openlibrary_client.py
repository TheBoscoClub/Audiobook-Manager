"""
Tests for the OpenLibrary API client.

Exercises the parsers, retry logic, and rate limiting of
``library/scripts/utils/openlibrary_client.py`` using mocked
``requests.Session`` responses — no network calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR / "scripts"))

from utils.openlibrary_client import (  # noqa: E402
    OpenLibraryClient,
    OpenLibraryEdition,
    RateLimitError,
)


def _mock_response(status_code: int = 200, json_data=None):
    """Build a mock ``requests.Response``-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    if status_code >= 400 and status_code != 404 and status_code != 429:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestInit:
    def test_default_params(self):
        client = OpenLibraryClient()
        assert client.delay == 0.6
        assert client.timeout == 30
        assert client.max_retries == 3
        assert "AudiobookLibrary" in client.session.headers["User-Agent"]

    def test_custom_params(self):
        client = OpenLibraryClient(rate_limit_delay=0.1, timeout=5, max_retries=1)
        assert client.delay == 0.1
        assert client.timeout == 5
        assert client.max_retries == 1


class TestRateLimit:
    def test_first_call_no_sleep(self):
        client = OpenLibraryClient(rate_limit_delay=0.01)
        with patch("time.sleep") as mock_sleep:
            client._rate_limit()
            # First call: elapsed is huge (time vs 0), so no sleep
            mock_sleep.assert_not_called()
        assert client.last_request_time > 0

    def test_second_call_sleeps_if_too_soon(self):
        client = OpenLibraryClient(rate_limit_delay=100.0)
        with patch("time.sleep") as mock_sleep, patch("time.time") as mock_time:
            mock_time.side_effect = [1000.0, 1000.0, 1000.0, 1000.1]
            client.last_request_time = 999.9  # recent
            client._rate_limit()
            # Should have slept for nearly the full delay
            assert mock_sleep.called


class TestGet:
    def test_200_returns_json(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(200, {"ok": True})
        result = client._get("http://example.com")
        assert result == {"ok": True}

    def test_404_returns_none(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(404)
        assert client._get("http://example.com") is None

    def test_429_triggers_retry_then_raises(self):
        client = OpenLibraryClient(rate_limit_delay=0.0, max_retries=2)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(429)
        with patch("time.sleep"), pytest.raises(RateLimitError):
            client._get("http://example.com")
        # Called 1 (initial) + 2 (retries) = 3 times
        assert client.session.get.call_count == 3

    def test_429_retry_then_success(self):
        client = OpenLibraryClient(rate_limit_delay=0.0, max_retries=3)
        client.session = MagicMock()
        client.session.get.side_effect = [_mock_response(429), _mock_response(200, {"data": "ok"})]
        with patch("time.sleep"):
            result = client._get("http://example.com")
        assert result == {"data": "ok"}

    def test_timeout_retries(self):
        client = OpenLibraryClient(rate_limit_delay=0.0, max_retries=2)
        client.session = MagicMock()
        client.session.get.side_effect = [
            requests.Timeout(),
            requests.Timeout(),
            requests.Timeout(),
        ]
        # With max_retries=2, returns None after 3 total attempts
        assert client._get("http://example.com") is None

    def test_timeout_then_success(self):
        client = OpenLibraryClient(rate_limit_delay=0.0, max_retries=3)
        client.session = MagicMock()
        client.session.get.side_effect = [
            requests.Timeout(),
            _mock_response(200, {"recovered": True}),
        ]
        result = client._get("http://example.com")
        assert result == {"recovered": True}

    def test_connection_error_returns_none(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.side_effect = requests.ConnectionError("no network")
        assert client._get("http://example.com") is None


class TestLookupIsbn:
    def test_strips_hyphens_and_spaces(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(
            200,
            {
                "key": "/books/OL123M",
                "title": "Sample",
                "isbn_10": ["1234567890"],
                "isbn_13": ["9781234567890"],
                "works": [{"key": "/works/OL999W"}],
                "publish_date": "2020",
                "publishers": ["ACME"],
            },
        )
        edition = client.lookup_isbn("978-1 234-5678 90")
        called_url = client.session.get.call_args[0][0]
        assert "9781234567890" in called_url
        assert isinstance(edition, OpenLibraryEdition)
        assert edition.isbn_13 == "9781234567890"
        assert edition.isbn_10 == "1234567890"
        assert edition.work_id == "OL999W"

    def test_returns_none_on_404(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(404)
        assert client.lookup_isbn("0000000000") is None


class TestSearch:
    def test_empty_returns_empty(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        assert client.search() == []

    def test_builds_query_from_title_and_author(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(
            200, {"docs": [{"title": "The Hobbit"}, {"title": "Hobbit 2"}]}
        )
        results = client.search(title="Hobbit", author="Tolkien", limit=5)
        called_url = client.session.get.call_args[0][0]
        assert "title=Hobbit" in called_url
        assert "author=Tolkien" in called_url
        assert "limit=5" in called_url
        assert len(results) == 2

    def test_isbn_in_query(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(200, {"docs": []})
        client.search(isbn="9780261103573")
        called_url = client.session.get.call_args[0][0]
        assert "isbn=9780261103573" in called_url

    def test_caps_at_limit(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        # Return more docs than limit — client should slice
        client.session.get.return_value = _mock_response(
            200, {"docs": [{"i": i} for i in range(10)]}
        )
        results = client.search(title="x", limit=3)
        assert len(results) == 3

    def test_missing_docs_key_returns_empty(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(200, {})
        assert client.search(title="x") == []


class TestGetWork:
    def test_normalizes_work_id_prefix(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(
            200,
            {
                "key": "/works/OL27479W",
                "title": "The Hobbit",
                "subjects": ["Fiction", {"name": "Fantasy"}],
                "description": "A novel.",
                "first_publish_year": 1937,
                "covers": [1, 2, 3],
                "authors": [{"author": {"key": "/authors/OL26320A"}}],
            },
        )
        work = client.get_work("/works/OL27479W")
        called_url = client.session.get.call_args[0][0]
        assert "/works/OL27479W.json" in called_url
        assert work.title == "The Hobbit"
        assert "Fiction" in work.subjects
        assert "Fantasy" in work.subjects
        assert work.description == "A novel."
        assert work.first_publish_year == 1937
        assert work.covers == [1, 2, 3]

    def test_description_as_dict(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(
            200, {"title": "x", "description": {"value": "dict desc"}}
        )
        work = client.get_work("OL1W")
        assert work.description == "dict desc"

    def test_404_returns_none(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(404)
        assert client.get_work("OLxxx") is None


class TestGetAuthor:
    def test_strips_authors_prefix(self):
        client = OpenLibraryClient(rate_limit_delay=0.0)
        client.session = MagicMock()
        client.session.get.return_value = _mock_response(200, {"name": "Tolkien"})
        result = client.get_author("/authors/OL26320A")
        called_url = client.session.get.call_args[0][0]
        assert "/authors/OL26320A.json" in called_url
        assert result == {"name": "Tolkien"}


class TestGetCoverUrl:
    def test_default_size(self):
        client = OpenLibraryClient()
        assert client.get_cover_url(12345) == "https://covers.openlibrary.org/b/id/12345-M.jpg"

    def test_custom_size(self):
        client = OpenLibraryClient()
        assert client.get_cover_url(99, size="L") == "https://covers.openlibrary.org/b/id/99-L.jpg"


class TestParseEdition:
    def test_non_list_isbn(self):
        client = OpenLibraryClient()
        data = {"key": "/books/OL1M", "title": "t", "isbn_10": "0-1-2", "isbn_13": "9-7-8"}
        edition = client._parse_edition(data)
        assert edition.isbn_10 == "0-1-2"
        assert edition.isbn_13 == "9-7-8"

    def test_no_works(self):
        client = OpenLibraryClient()
        edition = client._parse_edition({"key": "k", "title": "t"})
        assert edition.work_id is None


class TestParseWork:
    def test_empty_subjects(self):
        client = OpenLibraryClient()
        work = client._parse_work({"key": "/works/OL1W", "title": "t"})
        assert work.subjects == []
        assert work.description is None

    def test_mixed_author_refs(self):
        client = OpenLibraryClient()
        data = {
            "key": "/works/OL1W",
            "title": "t",
            "authors": [
                {"author": {"key": "/authors/OL1A"}},
                {"author": {}},  # missing key
                "not-a-dict",  # wrong type
            ],
        }
        work = client._parse_work(data)
        assert work.authors == ["/authors/OL1A"]
