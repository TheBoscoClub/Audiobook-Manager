"""Tests for localization metadata lookup (Douban + DeepL fallback).

Covers ``localization/metadata/douban.py`` and ``localization/metadata/lookup.py``.
All HTTP calls are mocked via ``requests_mock``. The DeepL translator is a
stub so we verify orchestration without hitting the real API.
"""

from __future__ import annotations

from typing import cast

import requests
from localization.metadata.douban import DOUBAN_API_URL, DoubanClient
from localization.metadata.lookup import BookMetadata, MetadataLookup
from localization.translation.deepl_translate import DeepLTranslator

# --- DoubanClient.search_by_isbn ---------------------------------------------


class TestDoubanSearchByIsbn:
    def test_no_api_key_returns_none(self) -> None:
        client = DoubanClient(api_key="")
        assert client.search_by_isbn("9787111000000") is None

    def test_successful_lookup_returns_metadata(self, requests_mock) -> None:
        requests_mock.get(
            f"{DOUBAN_API_URL}/isbn/9787111000000",
            json={"title": "三体", "author": ["刘慈欣"], "translator": ["Ken Liu"]},
        )
        client = DoubanClient(api_key="test-key")
        result = client.search_by_isbn("9787111000000")
        assert result == {
            "title": "三体",
            "author": "刘慈欣",
            "translator": "Ken Liu",
            "source": "douban",
        }

    def test_multiple_authors_joined_with_comma(self, requests_mock) -> None:
        requests_mock.get(
            f"{DOUBAN_API_URL}/isbn/9787111000001",
            json={
                "title": "合著之书",
                "author": ["作者甲", "作者乙"],
                "translator": ["译者一", "译者二"],
            },
        )
        client = DoubanClient(api_key="test-key")
        result = client.search_by_isbn("9787111000001")
        assert result is not None
        assert result["author"] == "作者甲, 作者乙"
        assert result["translator"] == "译者一, 译者二"

    def test_404_returns_none(self, requests_mock) -> None:
        requests_mock.get(f"{DOUBAN_API_URL}/isbn/missing", status_code=404)
        client = DoubanClient(api_key="test-key")
        assert client.search_by_isbn("missing") is None

    def test_http_error_returns_none(self, requests_mock) -> None:
        requests_mock.get(f"{DOUBAN_API_URL}/isbn/broken", status_code=500)
        client = DoubanClient(api_key="test-key")
        assert client.search_by_isbn("broken") is None

    def test_connection_error_returns_none(self, requests_mock) -> None:
        requests_mock.get(f"{DOUBAN_API_URL}/isbn/net-fail", exc=requests.ConnectionError("boom"))
        client = DoubanClient(api_key="test-key")
        assert client.search_by_isbn("net-fail") is None

    def test_missing_optional_fields(self, requests_mock) -> None:
        """When author/translator arrays are absent, empty strings are returned."""
        requests_mock.get(f"{DOUBAN_API_URL}/isbn/sparse", json={"title": "Bare Book"})
        client = DoubanClient(api_key="test-key")
        result = client.search_by_isbn("sparse")
        assert result == {"title": "Bare Book", "author": "", "translator": "", "source": "douban"}


# --- DoubanClient.search_by_title --------------------------------------------


class TestDoubanSearchByTitle:
    def test_no_api_key_returns_none(self) -> None:
        assert DoubanClient(api_key="").search_by_title("标题") is None

    def test_title_only_query(self, requests_mock) -> None:
        def _match(request) -> bool:
            return request.qs.get("q") == ["三体"]

        requests_mock.get(
            f"{DOUBAN_API_URL}/search",
            json={"books": [{"title": "三体", "author": ["刘慈欣"]}]},
            additional_matcher=_match,
        )
        client = DoubanClient(api_key="test-key")
        result = client.search_by_title("三体")
        assert result is not None
        assert result["title"] == "三体"
        assert result["source"] == "douban"

    def test_title_plus_author_combined_in_query(self, requests_mock) -> None:
        captured = {}

        def _match(request) -> bool:
            captured["q"] = request.qs.get("q")
            return True

        requests_mock.get(
            f"{DOUBAN_API_URL}/search",
            json={"books": [{"title": "The Three Body Problem", "author": ["Liu Cixin"]}]},
            additional_matcher=_match,
        )
        client = DoubanClient(api_key="test-key")
        result = client.search_by_title("The Three Body Problem", "Liu Cixin")
        assert result is not None
        # requests_mock lowercases query-string values on capture; we just
        # verify title+author were concatenated into the single ``q`` param.
        assert captured["q"] == ["the three body problem liu cixin"]

    def test_empty_books_list_returns_none(self, requests_mock) -> None:
        requests_mock.get(f"{DOUBAN_API_URL}/search", json={"books": []})
        client = DoubanClient(api_key="test-key")
        assert client.search_by_title("nothing") is None

    def test_http_error_returns_none(self, requests_mock) -> None:
        requests_mock.get(f"{DOUBAN_API_URL}/search", status_code=503)
        client = DoubanClient(api_key="test-key")
        assert client.search_by_title("fail") is None


# --- MetadataLookup orchestration -------------------------------------------


class _StubDouban:
    """Controllable DoubanClient stand-in for orchestration tests."""

    def __init__(self, isbn_result: dict | None = None, title_result: dict | None = None) -> None:
        self.isbn_result = isbn_result
        self.title_result = title_result
        self.isbn_calls: list[str] = []
        self.title_calls: list[tuple[str, str]] = []

    def search_by_isbn(self, isbn: str) -> dict | None:
        self.isbn_calls.append(isbn)
        return self.isbn_result

    def search_by_title(self, title: str, author: str = "") -> dict | None:
        self.title_calls.append((title, author))
        return self.title_result


class _StubDeepL:
    """DeepL translator stand-in; can be told to succeed or raise."""

    def __init__(self, output: list[str] | None = None, raise_exc: bool = False) -> None:
        self.output = output or []
        self.raise_exc = raise_exc
        self.calls: list[tuple[list[str], str]] = []

    def translate(self, texts, target_locale: str):
        self.calls.append((list(texts), target_locale))
        if self.raise_exc:
            raise RuntimeError("deepl boom")
        return list(self.output)


class TestMetadataLookup:
    def test_empty_lookup_returns_none(self) -> None:
        assert MetadataLookup().lookup("x", "y", "zh-Hans") is None

    def test_douban_isbn_wins(self) -> None:
        douban = _StubDouban(isbn_result={"title": "书", "author": "作者", "translator": "译者"})
        lookup = MetadataLookup(douban_client=cast(DoubanClient, douban))
        meta = lookup.lookup("Book", "Author", "zh-Hans", isbn="9780000000000")
        assert isinstance(meta, BookMetadata)
        assert meta.title == "书"
        assert meta.author_display == "作者"
        assert meta.translator == "译者"
        assert meta.source == "douban"
        # When ISBN hits we must not fall back to title search.
        assert douban.title_calls == []

    def test_douban_title_fallback_when_isbn_misses(self) -> None:
        douban = _StubDouban(
            isbn_result=None, title_result={"title": "书", "author": "作者"}  # no translator key
        )
        lookup = MetadataLookup(douban_client=cast(DoubanClient, douban))
        meta = lookup.lookup("Book", "Author", "zh-Hans", isbn="9780000000001")
        assert meta is not None
        assert meta.source == "douban"
        assert meta.translator == ""  # default for missing key
        assert douban.isbn_calls == ["9780000000001"]
        assert douban.title_calls == [("Book", "Author")]

    def test_douban_title_search_when_no_isbn(self) -> None:
        douban = _StubDouban(title_result={"title": "书", "author": "作者"})
        lookup = MetadataLookup(douban_client=cast(DoubanClient, douban))
        meta = lookup.lookup("Book", "Author", "zh-Hans")
        assert meta is not None
        assert meta.source == "douban"
        assert douban.isbn_calls == []

    def test_deepl_fallback_when_douban_misses(self) -> None:
        douban = _StubDouban(isbn_result=None, title_result=None)
        deepl = _StubDeepL(output=["书", "作者"])
        lookup = MetadataLookup(
            douban_client=cast(DoubanClient, douban), deepl_translator=cast(DeepLTranslator, deepl)
        )
        meta = lookup.lookup("Book", "Author", "zh-Hans")
        assert meta is not None
        assert meta.title == "书"
        assert meta.author_display == "作者"
        assert meta.translator == ""
        assert meta.source == "deepl"
        assert deepl.calls == [(["Book", "Author"], "zh-Hans")]

    def test_deepl_only_when_no_douban(self) -> None:
        deepl = _StubDeepL(output=["T", "A"])
        lookup = MetadataLookup(deepl_translator=cast(DeepLTranslator, deepl))
        meta = lookup.lookup("Title", "Auth", "fr")
        assert meta is not None
        assert meta.source == "deepl"

    def test_deepl_exception_returns_none(self) -> None:
        douban = _StubDouban()
        deepl = _StubDeepL(raise_exc=True)
        lookup = MetadataLookup(
            douban_client=cast(DoubanClient, douban), deepl_translator=cast(DeepLTranslator, deepl)
        )
        assert lookup.lookup("Book", "Author", "zh-Hans") is None
